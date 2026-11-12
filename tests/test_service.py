"""Public provider admission and real shared rewriting; no native or hardware claim."""

from pathlib import Path

import pytest
from merlin.api import compile
from merlin.compiler.request import CompileOptions, CompileRequest, Source
from merlin.development.targetgen.plugins import load_module

ROOT = Path(__file__).resolve().parents[1]
SOURCE = """builtin.module {
  func.func @fixture(%a: tensor<2x2xi8>, %b: tensor<2x2xi8>) -> tensor<2x2xi32> {
    %z = arith.constant 0 : i32
    %e = tensor.empty() : tensor<2x2xi32>
    %f = linalg.fill ins(%z : i32) outs(%e : tensor<2x2xi32>) -> tensor<2x2xi32>
    %r = linalg.matmul ins(%a, %b : tensor<2x2xi8>, tensor<2x2xi8>)
         outs(%f : tensor<2x2xi32>) -> tensor<2x2xi32>
    func.return %r : tensor<2x2xi32>
  }
}
"""


def selected(tmp_path, **parameters):
    values = {
        "kodiak.unit": "saturn_opu",
        "kodiak.config": "selected-config",
        "kodiak.emit": "prepared-ir",
    }
    values.update(parameters)
    return CompileRequest(
        Source(SOURCE, kind="upstream-mlir"),
        tmp_path / "result",
        CompileOptions(parameters=values),
    )


@pytest.fixture
def service():
    return load_module(ROOT, "service.py", package_name="kodiak_ordinary_service")


def test_admission_refuses_implicit_or_image_routes_before_output(service, tmp_path):
    request = selected(tmp_path)
    for parameters, match in (
        ({"kodiak.emit": "elf"}, "explicitly select"),
        ({"other.option": True}, "unsupported"),
        (
            {"kodiak.emit": "object", "kodiak.cc": "/absent/compiler"},
            "available C compiler",
        ),
    ):
        request = selected(tmp_path, **parameters)
        with pytest.raises((ValueError, FileNotFoundError), match=match):
            service.compile(request)
        assert not request.output.exists()
    request = selected(tmp_path)
    with pytest.raises(ValueError, match="platform images"):
        service.validate(
            CompileRequest(
                request.source,
                request.output,
                CompileOptions(
                    platform="zephyr", parameters=request.options.parameters
                ),
            )
        )
    assert not request.output.exists()


def test_actual_missing_rtl_refuses_before_output(service, tmp_path, monkeypatch):
    from merlin.common import paths

    monkeypatch.setattr(paths, "env", lambda name: None)
    request = selected(tmp_path)
    with pytest.raises(FileNotFoundError, match="unset"):
        service.compile(request)
    assert not request.output.exists()


def test_public_partial_ir_preserves_real_rewrite_and_truthful_artifacts(
    service, tmp_path, monkeypatch
):
    # Geometry is an explicit fixture; the real target selector and shared rewrite run.
    # This checks transport/admission, not selected-RTL geometry or native execution.
    driver = service
    provider = driver.adapter("saturn_opu")
    monkeypatch.setattr(
        driver,
        "selection",
        lambda unit, config: (provider, None, 2, 16, []),
    )
    request = selected(tmp_path)
    result = compile(
        request.source, backend=service, output=request.output, options=request.options
    )
    assert result.backend == "kodiak"
    assert result.deployment is None
    assert result.metadata["routed"] == 1
    assert result.metadata["execution"] == "not_run"
    assert result.metadata["qualification"] == "not_established"
    assert {artifact.kind for artifact in result.artifacts} == {
        "partial-mlir",
        "matrix-signatures",
        "matrix-build-receipt",
    }
    assert "bufferization.access" in (request.output / "prepared.mlir").read_text()
    assert (request.output / "compilation.json").is_file()
    with pytest.raises(FileExistsError):
        service.compile(request)


def test_empty_admission_remains_a_failure(service, tmp_path, monkeypatch):
    driver = service
    provider = driver.adapter("saturn_opu")
    monkeypatch.setattr(
        driver, "selection", lambda unit, config: (provider, None, 3, 16, [])
    )
    request = selected(tmp_path)
    with pytest.raises(ValueError, match="no contractions admitted"):
        compile(
            request.source,
            backend=service,
            output=request.output,
            options=request.options,
        )
    assert (request.output / "failure.json").is_file()
    assert not (request.output / "compilation.json").exists()
