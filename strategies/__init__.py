from .sma_cross import SmaCrossover

# Add new strategies here; the UI picks them up automatically.
REGISTRY = {s.name: s for s in [SmaCrossover]}
