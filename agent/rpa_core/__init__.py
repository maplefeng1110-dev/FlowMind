"""FlowMind RPA core engine (the reusable base).

DSL interpreter, browser-driver bridge, visual self-healing, failure snapshots and
result sink. The ``rpa_flow`` plugin is a thin shell over this package, and other
browser-automation plugins can build on it too. Plugins that don't touch the
browser (Excel, email, HTTP, OCR…) stay independent and do NOT depend on this.
"""
