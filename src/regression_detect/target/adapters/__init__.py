"""Adapters that let the detector regression-test any text-in/text-out feature.

The built-in ticket summarizer is one target among several, not the only one a
run can measure. Everything stage 01 needs from a feature under test is the
`Target` protocol in `base.py`: an id, a `run(input_text) -> str`, and the
provenance that pins which feature produced a run's outputs.
"""
