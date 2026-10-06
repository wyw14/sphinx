"""Test the offline comparison of intersphinx inventories."""

from __future__ import annotations

import zlib
from typing import TYPE_CHECKING

import pytest

from sphinx.ext.intersphinx._cli import diff_main, inspect_main, main
from sphinx.ext.intersphinx._diff import (
    ADDED,
    CANDIDATES_CHANGED,
    DISPLAY_CHANGED,
    REMOVED,
    URI_CHANGED,
    CandidateChange,
    EntryChange,
    InventoryDiff,
    ReferenceLocation,
    format_diff_report,
    parse_references,
)
from sphinx.util.inventory import InventoryFile

if TYPE_CHECKING:
    from sphinx.util.typing import Inventory


def make_inventory(
    lines: tuple[str, ...],
    *,
    project: str = 'foo',
    version: str = '2.0',
    compresslevel: int = 9,
) -> Inventory:
    body = ''.join(f'{line}\n' for line in lines)
    data = (
        f'# Sphinx inventory version 2\n'
        f'# Project: {project}\n'
        f'# Version: {version}\n'
        f'# The remainder of this file is compressed with zlib.\n'
    ).encode() + zlib.compress(body.encode(), compresslevel)
    return InventoryFile.loads(data, uri='').data


# Lines shared between inventories that are semantically identical but
# authored with different abbreviations, ordering and compression.
OLD_LINES = (
    'stable py:class 0 stable.html#stable -',
    'mod py:module 0 m.html#module-mod -',
    'thing py:data 0 t.html#x thing',
    'moved py:method 0 api.html#moved-old -',
    'relabel py:func 0 f.html#relabel Old label',
    'gone py:function 0 gone.html#gone -',
    'candidate py:class 0 c.html#candidate-as-class -',
    'widened py:class 0 w.html#widened -',
)

NEW_LINES = (
    # reordered, different compression level, equivalent abbreviations
    'widened py:class 0 w.html#widened -',
    'candidate py:function 0 c.html#candidate-as-function -',
    'gone.py? no',  # invalid line, ignored by the inventory parser
    'relabel py:func 0 f.html#relabel New label',
    'moved py:method 0 api.html#moved-new -',
    'thing py:data 0 t.html#x -',
    'mod py:module 0 m.html#module-$ -',
    'stable py:class 0 stable.html#stable stable',
    'brand_new py:attr 0 new.html#brand_new -',
    'widened py:method 0 w.html#widened-method -',
)


def make_diff() -> InventoryDiff:
    old = make_inventory(OLD_LINES, compresslevel=9)
    new = make_inventory(NEW_LINES, compresslevel=1)
    return InventoryDiff.from_inventories(old, new)


def test_no_semantic_change_for_compression_order_and_abbreviations() -> None:
    # Same objects, but reordered, compressed at another level and using the
    # '$' URI and '-' display-name abbreviations differently.
    old_lines = (
        'a py:class 0 a.html#A -',
        'b py:module 0 b.html#module-b -',
        'c py:data 0 c.html#c c',
    )
    new_lines = (
        'c py:data 0 c.html#c -',
        'b py:module 0 b.html#module-$ b',
        'a py:class 0 a.html#A a',
    )
    old = make_inventory(old_lines, compresslevel=9)
    new = make_inventory(new_lines, compresslevel=1)

    diff = InventoryDiff.from_inventories(old, new)

    assert diff.is_empty()
    assert diff.removed == ()
    assert diff.added == ()
    assert diff.uri_changed == ()
    assert diff.display_changed == ()
    assert diff.candidates_changed == ()
    assert not diff.metadata_changed


def test_diff_categories() -> None:
    diff = make_diff()

    assert [c.name for c in diff.removed] == ['gone']
    assert diff.removed[0] == EntryChange(
        'py:function', 'gone', diff.removed[0].old, None
    )
    assert diff.removed[0].old.uri == 'gone.html#gone'

    assert [c.name for c in diff.added] == ['brand_new']

    assert [(c.objtype, c.name) for c in diff.uri_changed] == [('py:method', 'moved')]
    assert diff.uri_changed[0].old.uri == 'api.html#moved-old'
    assert diff.uri_changed[0].new.uri == 'api.html#moved-new'
    assert diff.uri_changed[0].uri_changed
    assert not diff.uri_changed[0].display_changed

    assert [(c.objtype, c.name) for c in diff.display_changed] == [
        ('py:func', 'relabel')
    ]
    assert diff.display_changed[0].old.display_name == 'Old label'
    assert diff.display_changed[0].new.display_name == 'New label'
    assert diff.display_changed[0].display_changed
    assert not diff.display_changed[0].uri_changed

    # 'candidate' changed type entirely; 'widened' gained a new candidate.
    assert [c.name for c in diff.candidates_changed] == [
        'candidate',
        'widened',
    ]
    candidate = diff.candidates_changed[0]
    assert isinstance(candidate, CandidateChange)
    assert candidate.old_types == ('py:class',)
    assert candidate.new_types == ('py:function',)
    widened = diff.candidates_changed[1]
    assert widened.old_types == ('py:class',)
    assert widened.new_types == ('py:class', 'py:method')

    # A name surviving under another type is not "disappeared".
    assert not any(c.name == 'candidate' for c in diff.removed)


def test_project_metadata_change_is_reported() -> None:
    old = make_inventory(('a py:class 0 a.html#A -',), version='2.0')
    new = make_inventory(('a py:class 0 a.html#A -',), version='2.1')

    diff = InventoryDiff.from_inventories(old, new)

    assert diff.is_empty()
    assert diff.metadata_changed
    assert diff.old_project == ('foo', '2.0')
    assert diff.new_project == ('foo', '2.1')


def test_candidate_change_plus_shared_target_change() -> None:
    # A new candidate type appears while the surviving shared type also
    # moves to a different URI: both changes are reported.
    old = make_inventory(('thing py:class 0 t.html#thing-old -',))
    new = make_inventory((
        'thing py:class 0 t.html#thing-new -',
        'thing py:method 0 t.html#thing-method -',
    ))

    diff = InventoryDiff.from_inventories(old, new)

    assert [c.name for c in diff.candidates_changed] == ['thing']
    assert diff.candidates_changed[0].new_types == ('py:class', 'py:method')
    assert [(c.objtype, c.name) for c in diff.uri_changed] == [('py:class', 'thing')]
    assert diff.uri_changed[0].old.uri == 't.html#thing-old'
    assert diff.uri_changed[0].new.uri == 't.html#thing-new'


def test_parse_references() -> None:
    text = (
        '# a comment\n'
        '\n'
        'doc/a.rst:1\tpy:class\tFoo\n'
        'doc/b.rst:2\tpy:func:bar\n'
        'doc/c.rst:3\tBaz\n'
    )
    references = parse_references(text)

    assert references == (
        ReferenceLocation('doc/a.rst:1', 'Foo', 'py:class'),
        ReferenceLocation('doc/b.rst:2', 'bar', 'py:func'),
        ReferenceLocation('doc/c.rst:3', 'Baz', None),
    )


def test_parse_references_invalid_line() -> None:
    with pytest.raises(ValueError, match='invalid reference line'):
        parse_references('only-one-column\n')


def test_reference_locations_are_correlated() -> None:
    diff = make_diff()
    references = (
        # typed reference to a fully removed object
        ReferenceLocation('doc/gone.rst:1', 'gone', 'py:function'),
        # typed reference to the old (now replaced) candidate type
        ReferenceLocation('doc/cand.rst:2', 'candidate', 'py:class'),
        # unqualified reference to the same name
        ReferenceLocation('doc/cand-any.rst:3', 'candidate'),
        # typed reference whose target moved
        ReferenceLocation('doc/moved.rst:4', 'moved', 'py:method'),
        # unqualified reference to a name with a new candidate
        ReferenceLocation('doc/widened.rst:5', 'widened'),
        # reference to an unchanged object: not reported
        ReferenceLocation('doc/stable.rst:6', 'stable', 'py:class'),
        # reference that did not resolve in the old inventory either
        ReferenceLocation('doc/never.rst:7', 'never-existed', 'py:class'),
    )

    impacts: dict[str, dict] = {
        category: {}
        for category in (
            REMOVED,
            URI_CHANGED,
            DISPLAY_CHANGED,
            CANDIDATES_CHANGED,
            ADDED,
        )
    }
    for category, change, locations in diff.iter_changes(references):
        if isinstance(change, CandidateChange):
            impacts[category][change.name] = locations
        else:
            impacts[category][change.objtype, change.name] = locations

    assert impacts[REMOVED] == {
        ('py:function', 'gone'): ('doc/gone.rst:1',),
    }
    assert impacts[URI_CHANGED] == {
        ('py:method', 'moved'): ('doc/moved.rst:4',),
    }
    assert impacts[DISPLAY_CHANGED] == {
        ('py:func', 'relabel'): (),
    }
    assert impacts[CANDIDATES_CHANGED] == {
        'candidate': ('doc/cand-any.rst:3', 'doc/cand.rst:2'),
        'widened': ('doc/widened.rst:5',),
    }

    # The unchanged and never-resolved references appear nowhere.
    all_locations = [
        location
        for _category, _change, locations in diff.iter_changes(references)
        for location in locations
    ]
    assert 'doc/stable.rst:6' not in all_locations
    assert 'doc/never.rst:7' not in all_locations


def test_format_report_contains_sections_and_locations() -> None:
    diff = make_diff()
    references = (
        ReferenceLocation('doc/gone.rst:1', 'gone', 'py:function'),
        ReferenceLocation('doc/moved.rst:4', 'moved', 'py:method'),
    )

    report = format_diff_report(
        diff,
        old_label='old.inv',
        new_label='new.inv',
        references=references,
    )

    assert 'old: old.inv' in report
    assert 'new: new.inv' in report
    assert 'Objects disappeared (1):' in report
    assert 'py:function  gone' in report
    assert 'Target URIs changed (1):' in report
    assert 'api.html#moved-old' in report
    assert 'api.html#moved-new' in report
    assert 'Display information changed (1):' in report
    assert 'Old label' in report
    assert 'Same-name candidates changed (2):' in report
    assert 'doc/gone.rst:1' in report
    assert 'doc/moved.rst:4' in report
    assert '1 disappeared, 1 target URI changed, 1 display information ' in report


def _write_inventory(tmp_path, name: str, lines: tuple[str, ...], **kwargs):
    body = ''.join(f'{line}\n' for line in lines).encode()
    version = kwargs.get('version', '2.0')
    project = kwargs.get('project', 'foo')
    level = kwargs.get('compresslevel', 9)
    data = (
        f'# Sphinx inventory version 2\n'
        f'# Project: {project}\n'
        f'# Version: {version}\n'
        f'# The remainder of this file is compressed with zlib.\n'
    ).encode() + zlib.compress(body, level)
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_diff_main_files(tmp_path, capsys) -> None:
    old_path = _write_inventory(tmp_path, 'old.inv', OLD_LINES, compresslevel=9)
    new_path = _write_inventory(tmp_path, 'new.inv', NEW_LINES, compresslevel=1)
    references_path = tmp_path / 'references.tsv'
    references_path.write_text(
        'doc/gone.rst:1\tpy:function\tgone\n# comment\ndoc/any.rst:2\tcandidate\n',
        encoding='utf-8',
    )

    result = diff_main([
        str(old_path),
        str(new_path),
        '--references',
        str(references_path),
    ])

    assert result == 0
    stdout, stderr = capsys.readouterr()
    assert stderr == ''
    assert 'Objects disappeared (1):' in stdout
    assert 'doc/gone.rst:1' in stdout
    assert 'doc/any.rst:2' in stdout
    assert 'Same-name candidates changed (2):' in stdout


def test_diff_main_references_equals_form(tmp_path, capsys) -> None:
    old_path = _write_inventory(tmp_path, 'old.inv', ('a py:class 0 a.html#A -',))
    new_path = _write_inventory(tmp_path, 'new.inv', ('b py:class 0 b.html#B -',))
    references_path = tmp_path / 'references.tsv'
    references_path.write_text('doc/a.rst:1\tpy:class\ta\n', encoding='utf-8')

    result = diff_main([
        f'--references={references_path}',
        str(old_path),
        str(new_path),
    ])

    assert result == 0
    stdout, stderr = capsys.readouterr()
    assert stderr == ''
    assert 'doc/a.rst:1' in stdout


def test_diff_main_no_changes(tmp_path, capsys) -> None:
    old_path = _write_inventory(tmp_path, 'old.inv', OLD_LINES, compresslevel=9)
    new_path = _write_inventory(tmp_path, 'new.inv', OLD_LINES, compresslevel=1)

    result = diff_main([str(old_path), str(new_path)])

    assert result == 0
    stdout, stderr = capsys.readouterr()
    assert stderr == ''
    assert '0 disappeared, 0 target URI changed, 0 display information ' in stdout


def test_diff_main_wrong_arguments(capsys) -> None:
    assert diff_main([]) == 1
    _, stderr = capsys.readouterr()
    assert 'must specify exactly two inventory file paths' in stderr

    assert diff_main(['only-one']) == 1
    _, stderr = capsys.readouterr()
    assert 'must specify exactly two inventory file paths' in stderr


def test_diff_main_unknown_option(tmp_path, capsys) -> None:
    old_path = _write_inventory(tmp_path, 'old.inv', ('a py:class 0 a.html#A -',))
    new_path = _write_inventory(tmp_path, 'new.inv', ('a py:class 0 a.html#A -',))

    result = diff_main(['--bogus', str(old_path), str(new_path)])

    assert result == 1
    _, stderr = capsys.readouterr()
    assert 'unknown option' in stderr


def test_diff_main_missing_references_file(tmp_path, capsys) -> None:
    old_path = _write_inventory(tmp_path, 'old.inv', ('a py:class 0 a.html#A -',))
    new_path = _write_inventory(tmp_path, 'new.inv', ('a py:class 0 a.html#A -',))

    result = diff_main([
        str(old_path),
        str(new_path),
        '--references',
        str(tmp_path / 'absent.tsv'),
    ])

    assert result == 1
    _, stderr = capsys.readouterr()
    assert 'Error' in stderr


def test_main_dispatch(tmp_path, capsys) -> None:
    old_path = _write_inventory(tmp_path, 'old.inv', ('a py:class 0 a.html#A -',))
    new_path = _write_inventory(tmp_path, 'new.inv', ('a py:class 0 a.html#A -',))

    # zero arguments: usage error
    assert main([]) == 1

    # one argument: inspection, unchanged
    assert main([str(old_path)]) == 0
    stdout, _ = capsys.readouterr()
    assert stdout.startswith('py:class\n')

    # two arguments: comparison
    assert main([str(old_path), str(new_path)]) == 0
    stdout, _ = capsys.readouterr()
    assert 'Intersphinx inventory comparison' in stdout

    # inspect_main itself is unchanged
    assert inspect_main([]) == 1
