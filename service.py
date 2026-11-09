"""Selected chip contracts and ordinary prepared-IR compilation service."""

from merlin.compiler.lower import prepared

name = "kodiak"
_REMAINING = ("CPU/RVV lowering", "ABI admission", "linking", "platform construction")


def adapter(unit):
    from .src.kodiak.lower.interface import for_unit

    return for_unit(unit)


def selection(unit, config):
    provider = adapter(unit)
    contract = provider.load_contract(unit)
    edge, alignment = contract.geometry(config)
    sources = [
        *(contract.source(key) for key in sorted(contract.sources)),
        *contract.config_scala(),
        *contract.mixin_scala(),
    ]
    if unit == "saturn_vme":
        sources.append(contract.core_source())
        if contract.host.get("source"):
            sources.append(contract.root() / str(contract.host["source"]))
    return provider, contract, edge, alignment, sources


def validate(request):
    prepared.validate(request, namespace=name, select=selection)


def compile(request):
    return prepared.compile(
        request, namespace=name, select=selection, remaining=_REMAINING
    )
