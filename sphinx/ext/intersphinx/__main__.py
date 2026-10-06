"""Command line interface for the intersphinx extension."""

from __future__ import annotations

import logging as _logging
import sys

from sphinx.ext.intersphinx._cli import main

_logging.basicConfig()

raise SystemExit(main(sys.argv[1:]))
