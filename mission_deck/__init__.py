"""mission-deck: a Python desktop tool for managing courtroom AV equipment.

The package strictly decouples application logic from environment data:
all device/room configuration is loaded at runtime from an external
``config.json`` (never committed). See ``config.example.json`` for the schema.
"""

__version__ = "0.2.0"
__app_name__ = "mission-deck"
