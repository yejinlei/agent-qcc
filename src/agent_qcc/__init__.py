"""LangGraph agent over the Qichacha (企查查) connector skill."""

__version__ = "0.1.0"


# 4R+1 pipeline stages, exposed for tests and the architecture diagram.
STAGES = ("route", "reason", "retrieve", "reflect", "respond")
