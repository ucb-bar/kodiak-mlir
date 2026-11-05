/* opu_tile_loop.c -- a plain OPU GEMM: one tile (64x64 on KodiakOPUConfig) at a time, one OPMACC per
 * reduction row.
 *
 * Hand-written against the generated header. Every OPU instruction word comes from saturn_opu.h, which
 * Merlin writes at build time from the encodings it derives from the OPU's RTL; nothing here is a
 * hand-typed opcode. Copy this file to start a kernel of your own (see the README, "Write your own kernel").
 *
 * The ABI (what Merlin calls, for every int8 contraction it routes to the OPU):
 *
 *   C[i][j] = (bias ? bias[j] : 0) + sum_k at[k*m + i] * b[k*n + j]       int32, wrapping; C is WRITTEN
 *
 *   at  the left operand, K-major (k rows of m), packed by Merlin into an aligned scratch buffer
 *   b   the right operand, K-major (k rows of n), the weights in place
 *   c   the int32 accumulators, row-major m x n; Merlin requantizes them to int8 after this returns
 *   bias may be NULL (the model's shim always passes NULL; kernel-test also checks a bias)
 *   m, n, k >= 1; at, b and c are aligned to OPU_ALIGN_BYTES
 *
 * Register plan (this kernel's choice; the generated kernel uses the same one):
 *   matrix register 1      the accumulator tile
 *   v0..v3 (one e32 group) the bias row broadcast into the tile, and each row read back out of it
 *   v4                     one row of B (the tile's columns)
 *   v5 / v6                one row of A (the tile's rows), alternated between reduction steps: the unit
 *                          stops advertising its read of the A register one iteration early, so the next
 *                          step must load into the OTHER register
 *
 * Each reduction step is ONE asm statement (configure, load, configure, load, accumulate), so the compiler
 * never places a vector-length change of its own between the loads and the OPMACC.
 *
 * Not handled here, unlike the generated kernel: the taped-out part computes a tile with a partial N (n not
 * a multiple of the tile edge) wrongly unless B is zero-padded to a full tile. Spike's OPU model and the
 * FPGA revision do not have that erratum.
 */
#include "saturn_opu.h"

/* One accumulator row at e32 spans OPU_ACC_LMUL registers. */
#define ACC_VTYPE "e32, m" OPU_STR(OPU_ACC_LMUL) ", ta, ma"

/* One reduction step: A row `ap` (ml lanes) into v<AREG>, B row `bp` (nl lanes) into v4, accumulate. */
#define OPU_STEP(AREG)                                                                     \
  asm volatile("vsetvli t0, zero, e8, m1, ta, ma\n\t" /* zero the whole A register ...  */ \
               "vmv.v.i v" #AREG ", 0\n\t"                                                 \
               "vsetvli zero, %[ml], e8, m1, tu, ma\n\t" /* ... then load ml lanes */      \
               "vle8.v v" #AREG ", (%[ap])\n\t"                                            \
               "vsetvli zero, %[nl], e8, m1, ta, ma\n\t" /* nl columns */                  \
               "vle8.v v4, (%[bp])\n\t"                                                    \
               OPU_OPMACC(OPU_REG(1), OPU_REG(AREG), OPU_REG(4))                           \
               :: [ml] "r"(ml), [nl] "r"(nl), [ap] "r"(ap), [bp] "r"(bp)                  \
               : "t0", "memory")

/* C[0:ml, 0:nl] of one tile; c, at, b already point at the tile's corner. */
static void opu_tile(int32_t *c, const int8_t *at, const int8_t *b, const int32_t *bias,
                     size_t m, size_t n, size_t k, size_t ml, size_t nl)
{
  /* 1. Every row of the accumulator starts as the bias row (or zero). */
  if (bias) {
    asm volatile("vsetvli zero, %[nl], " ACC_VTYPE "\n\t"
                 "vle32.v v0, (%[bp])\n\t"
                 OPU_OPMVINBCAST(OPU_REG(1), OPU_REG(0), OPU_REG(0))
                 :: [nl] "r"(nl), [bp] "r"(bias) : "memory");
  } else {
    asm volatile("vsetvli zero, %[nl], " ACC_VTYPE "\n\t"
                 "vmv.v.i v0, 0\n\t"
                 OPU_OPMVINBCAST(OPU_REG(1), OPU_REG(0), OPU_REG(0))
                 :: [nl] "r"(nl) : "memory");
  }

  /* 2. One outer product per reduction row, alternating the A register. */
  for (size_t kk = 0; kk < k; ++kk) {
    const int8_t *ap = at + kk * m;
    const int8_t *bp = b + kk * n;
    if (kk & 1)
      OPU_STEP(6);
    else
      OPU_STEP(5);
  }

  /* 3. Read the tile out row by row (OPMVOUT moves row r of the accumulator into v0..v3) and store it. */
  asm volatile("vsetvli zero, %[nl], " ACC_VTYPE :: [nl] "r"(nl));
  for (size_t r = 0; r < ml; ++r) {
    asm volatile(OPU_OPMVOUT(OPU_REG(0), "%[r]", OPU_REG(1)) "\n\t"
                 "vse32.v v0, (%[cp])"
                 :: [r] "r"(r), [cp] "r"(c + r * n) : "memory");
  }
}

OPU_KERNEL(opu_tile_loop_gemm)
{
  /* The tile edge, asked of the hardware: VLMAX at e8/m1. */
  size_t te;
  asm volatile("vsetvli %0, zero, e8, m1, ta, ma" : "=r"(te));

  /* On a multi-hart image (Merlin compiles this file with -fopenmp there) each hart takes whole tiles:
   * tiles write disjoint parts of C, and each hart has its own OPU and accumulator. */
#ifdef _OPENMP
#pragma omp parallel for collapse(2) schedule(static)
#endif
  for (size_t i = 0; i < m; i += te)
    for (size_t j = 0; j < n; j += te) {
      const size_t ml = (m - i) < te ? (m - i) : te;
      const size_t nl = (n - j) < te ? (n - j) : te;
      opu_tile(c + i * n + j, at + i, b + j, bias ? bias + j : (const int32_t *)0, m, n, k, ml, nl);
    }
}
