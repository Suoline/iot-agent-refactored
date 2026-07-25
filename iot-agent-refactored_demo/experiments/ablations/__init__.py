from experiments.runner.system_registry import build_system_registry

ABLATION_IDS = [
    "-Decay",
    "-AsymFeedback",
    "-Governance",
    "-CandidateGate",
    "-ConflictHandling",
    "-FeatureAbsorption",
    "-Ripple",
    "-Split",
]
SYSTEM_REGISTRY = build_system_registry()
