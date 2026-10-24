"""One lock for importing and loading Hugging Face models.

Lanes B (detectors) and C (semantic matcher) run in parallel threads. Importing transformers or
loading two models at the same moment from two threads can break the library's lazy imports
("cannot import name 'AutoModel'"), which would silently switch a component off.
"""

import threading

HF_LOAD_LOCK = threading.RLock()
