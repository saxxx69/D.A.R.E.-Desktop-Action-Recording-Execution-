"""Intent Layer — Phase 4. Classifies actions STATIC/DYNAMIC; writes
``intent_registry.json`` and one markdown per action under ``docs/intents/``.
"""

from dare.intent.classifier import IntentClassifier, classify_action
from dare.intent.markdown_generator import IntentMarkdownGenerator
from dare.intent.builder import IntentBuilder

__all__ = [
    "IntentBuilder",
    "IntentClassifier",
    "IntentMarkdownGenerator",
    "classify_action",
]
