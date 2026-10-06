"""This module provides contains the code for intersphinx command-line utilities."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from sphinx.ext.intersphinx._diff import (
    InventoryDiff,
    format_diff_report,
    parse_references,
)
from sphinx.ext.intersphinx._load import (
    _fetch_inventory_data,
    _InvConfig,
    _load_inventory,
)

if TYPE_CHECKING:
    from sphinx.util.inventory import _Inventory

_USAGE = """\
Usage:
  python -m sphinx.ext.intersphinx INVENTORY
  python -m sphinx.ext.intersphinx [--references FILE] OLD_INVENTORY NEW_INVENTORY

With one inventory file, print out its contents.
With two inventory files, compare them offline and report semantic changes.

Options:
  --references FILE  correlate changes with previously collected reference
                     locations listed in FILE (tab-separated, one per line:
                     'location[\\tdomain:objtype]\\ttarget')."""


def _make_config() -> _InvConfig:
    return _InvConfig(
        intersphinx_cache_limit=5,
        intersphinx_timeout=None,
        tls_verify=False,
        tls_cacerts=None,
        user_agent='',
    )


def _load_inventory_file(filename: str) -> _Inventory:
    raw_data, _ = _fetch_inventory_data(
        target_uri='',
        inv_location=filename,
        config=_make_config(),
        srcdir=Path(),
        cache_path=None,
    )
    return _load_inventory(raw_data, target_uri='')


def inspect_main(argv: list[str], /) -> int:
    """Debug functionality to print out an inventory"""
    if len(argv) < 1:
        print(
            'Print out an inventory file.\n'
            'Error: must specify local path or URL to an inventory file.',
            file=sys.stderr,
        )
        return 1

    filename = argv[0]
    try:
        inv = _load_inventory_file(filename)
        for key in sorted(inv.data):
            print(key)
            inv_entries = sorted(inv.data[key].items())
            for entry, inv_item in inv_entries:
                display_name = inv_item.display_name
                display_name = display_name * (display_name != '-')
                print(f'    {entry:<40} {display_name:<40}: {inv_item.uri}')
    except ValueError as exc:
        print(exc.args[0] % exc.args[1:], file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'Unknown error: {exc!r}', file=sys.stderr)
        return 1
    else:
        return 0


def _parse_diff_args(argv: list[str]) -> tuple[list[str], str | None]:
    """Parse the positional inventory paths and the references option."""
    paths: list[str] = []
    references: str | None = None
    iterator = iter(argv)
    for arg in iterator:
        if arg == '--references':
            try:
                references = next(iterator)
            except StopIteration:
                msg = 'Error: --references requires a file argument.'
                raise ValueError(msg) from None
        elif arg.startswith('--references='):
            references = arg.removeprefix('--references=')
        elif arg.startswith('-'):
            msg = f'Error: unknown option: {arg}'
            raise ValueError(msg)
        else:
            paths.append(arg)
    return paths, references


def diff_main(argv: list[str], /) -> int:
    """Compare two inventory files offline and print semantic changes."""
    try:
        paths, references_path = _parse_diff_args(argv)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return 1

    if len(paths) != 2:
        print(
            'Compare two inventory files.\n'
            'Error: must specify exactly two inventory file paths '
            '(old and new).',
            file=sys.stderr,
        )
        return 1

    old_path, new_path = paths
    try:
        old_inventory = _load_inventory_file(old_path)
        new_inventory = _load_inventory_file(new_path)
        references = ()
        if references_path is not None:
            text = Path(references_path).read_text(encoding='utf-8')
            references = parse_references(text)
    except (OSError, ValueError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'Unknown error: {exc!r}', file=sys.stderr)
        return 1

    diff = InventoryDiff.from_inventories(old_inventory.data, new_inventory.data)
    print(
        format_diff_report(
            diff,
            old_label=old_path,
            new_label=new_path,
            references=references,
        )
    )
    return 0


def main(argv: list[str], /) -> int:
    """Dispatch between inventory inspection and inventory comparison."""
    try:
        paths, references_path = _parse_diff_args(argv)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return 1

    if len(paths) == 1 and references_path is None:
        return inspect_main(paths)
    if len(paths) == 2:
        return diff_main(argv)

    print(_USAGE, file=sys.stderr)
    return 1
