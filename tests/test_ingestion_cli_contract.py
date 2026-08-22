from valuation_engine.live_indexers import SourceFetchError, MissingCredentialError


def test_operational_errors_are_explicit_exception_types():
    assert issubclass(SourceFetchError, RuntimeError)
    assert issubclass(MissingCredentialError, RuntimeError)
