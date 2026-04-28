"""
preflight.py — Pre-startup checks for Event Dashboard
Run automatically at the top of app.py BEFORE any ML library imports.

Currently checks:
  - spaCy model availability; downloads en_core_web_sm if missing.

Why this exists:
  On macOS, attempting to download or spawn subprocesses AFTER spaCy,
  PyTorch, or sentence-transformers are imported causes segmentation faults
  due to fork-after-ObjC/GCD-runtime initialisation. Running this check
  first — before any such import — is the only safe approach.
"""

import sys
import logging

logger = logging.getLogger(__name__)


def check_spacy_model() -> None:
    """
    Check whether any usable spaCy model is installed.
    If not, attempt to download en_core_web_sm via subprocess.
    Safe to call before any ML library is imported.
    """
    try:
        import spacy as _spacy
    except ImportError:
        # spaCy not installed at all — nlp_enhancer degrades gracefully.
        return

    for model in ('en_core_web_md', 'en_core_web_sm', 'en_core_web_lg'):
        try:
            _spacy.load(model, disable=['parser', 'lemmatizer'])
            logger.info('[Preflight] spaCy model found: %s', model)
            return
        except OSError:
            continue

    # No model found — safe to download here because no ML libs are loaded yet.
    logger.info('[Preflight] No spaCy model found — downloading en_core_web_sm...')
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, '-m', 'spacy', 'download', 'en_core_web_sm'],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode == 0:
            logger.info('[Preflight] en_core_web_sm downloaded successfully.')
        else:
            output = (result.stderr.strip() or result.stdout.strip())[:300]
            logger.warning(
                '[Preflight] spaCy model download failed (exit %d): %s\n'
                '  Fix: run  python -m spacy download en_core_web_sm',
                result.returncode, output
            )
    except Exception as e:
        logger.warning(
            '[Preflight] spaCy model download error: %s\n'
            '  Fix: run  python -m spacy download en_core_web_sm', e
        )


def run() -> None:
    """Run all preflight checks. Called once at the top of app.py."""
    check_spacy_model()