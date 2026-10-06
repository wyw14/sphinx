"""Offline semantic comparison of two intersphinx inventories.

This module compares two *fixed* inventory files (typically named
``objects.inv``) without accessing the network, to determine how upgrading
an external documentation set affects the references of a project.

Both inventories are first parsed into the same normalised in-memory
representation, so that purely representational differences -- namely the
zlib compression, the ordering of entries, the ``$`` URI abbreviation and
the ``-`` display-name abbreviation -- are not reported as changes.

The following *semantic* change categories are distinguished:

* objects that disappeared entirely,
* objects whose target URI changed,
* objects whose display information changed,
* names whose set of same-name candidates (object types) changed,
  which affects unqualified ``:any:``-style resolution.

The resulting diff can be combined with reference locations that have
previously been collected from the project, to report the documentation
locations impacted by each change.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from sphinx.util.inventory import _InventoryItem
    from sphinx.util.typing import Inventory

#: The object disappeared: present in the old inventory but not in the new
#: one, with no same-name object remaining under another type.
REMOVED = 'removed'

#: The target URI of an existing object changed.
URI_CHANGED = 'uri_changed'

#: The display information (display name, or project name/version) changed.
DISPLAY_CHANGED = 'display_changed'

#: The set of object types carrying a given name changed.
CANDIDATES_CHANGED = 'candidates_changed'

#: A new object appeared (informational; does not break existing references).
ADDED = 'added'


@dataclasses.dataclass(frozen=True, slots=True, order=True)
class InventoryEntry:
    """A normalised inventory entry.

    The target URI has the ``$`` abbreviation expanded by the inventory
    loader, and a display name of ``-`` (meaning "same as the object name")
    is expanded into *name*.
    """

    objtype: str
    name: str
    uri: str
    display_name: str

    @classmethod
    def from_item(cls, objtype: str, name: str, item: _InventoryItem) -> InventoryEntry:
        display_name = name if item.display_name == '-' else item.display_name
        return cls(objtype, name, item.uri, display_name)


@dataclasses.dataclass(frozen=True, slots=True)
class ReferenceLocation:
    """A location in the project where an external object is referenced.

    *objtype* is ``None`` for unqualified references (the ``:any:`` role),
    in which case only *target* (the object name) is known.
    """

    location: str
    target: str
    objtype: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class EntryChange:
    """A change concerning a single ``(objtype, name)`` object."""

    objtype: str
    name: str
    old: InventoryEntry | None
    new: InventoryEntry | None

    @property
    def uri_changed(self) -> bool:
        return (
            self.old is not None
            and self.new is not None
            and self.old.uri != self.new.uri
        )

    @property
    def display_changed(self) -> bool:
        return (
            self.old is not None
            and self.new is not None
            and self.old.display_name != self.new.display_name
        )


@dataclasses.dataclass(frozen=True, slots=True)
class CandidateChange:
    """The set of same-name candidates of *name* changed.

    Each tuple holds the candidates (one per object type) found in the
    corresponding inventory, sorted by object type.
    """

    name: str
    old: tuple[InventoryEntry, ...]
    new: tuple[InventoryEntry, ...]

    @property
    def old_types(self) -> tuple[str, ...]:
        return tuple(entry.objtype for entry in self.old)

    @property
    def new_types(self) -> tuple[str, ...]:
        return tuple(entry.objtype for entry in self.new)


@dataclasses.dataclass(frozen=True, slots=True)
class InventoryDiff:
    """The semantic difference between two inventories."""

    old_project: tuple[str, str] | None
    new_project: tuple[str, str] | None
    removed: tuple[EntryChange, ...]
    added: tuple[EntryChange, ...]
    uri_changed: tuple[EntryChange, ...]
    display_changed: tuple[EntryChange, ...]
    candidates_changed: tuple[CandidateChange, ...]

    @classmethod
    def from_inventories(cls, old: Inventory, new: Inventory) -> InventoryDiff:
        """Compare two loaded inventories.

        Changes of compression, entry ordering or the ``$``/``-``
        abbreviations are not considered semantic and are ignored.
        """
        old_entries, old_by_name = _normalise_inventory(old)
        new_entries, new_by_name = _normalise_inventory(new)

        removed: list[EntryChange] = []
        added: list[EntryChange] = []
        uri_changed: list[EntryChange] = []
        display_changed: list[EntryChange] = []
        candidates_changed: list[CandidateChange] = []

        # Objects only present in one inventory. When the name still exists
        # under another type, the change is reported as a candidate change
        # rather than a plain disappearance/appearance.
        for objtype, name in sorted(set(old_entries) - set(new_entries)):
            if name not in new_by_name:
                removed.append(
                    EntryChange(objtype, name, old_entries[objtype, name], None)
                )
        for objtype, name in sorted(set(new_entries) - set(old_entries)):
            if name not in old_by_name:
                added.append(
                    EntryChange(objtype, name, None, new_entries[objtype, name])
                )

        # Names present in both inventories: report candidate type-set
        # changes, and compare the entries of the types shared by both.
        for name in sorted(set(old_by_name) & set(new_by_name)):
            old_candidates = old_by_name[name]
            new_candidates = new_by_name[name]
            if set(old_candidates) != set(new_candidates):
                candidates_changed.append(
                    CandidateChange(
                        name,
                        tuple(old_candidates[t] for t in sorted(old_candidates)),
                        tuple(new_candidates[t] for t in sorted(new_candidates)),
                    )
                )
            for objtype in sorted(set(old_candidates) & set(new_candidates)):
                old_entry = old_candidates[objtype]
                new_entry = new_candidates[objtype]
                change = EntryChange(objtype, name, old_entry, new_entry)
                if change.uri_changed:
                    uri_changed.append(change)
                elif change.display_changed:
                    display_changed.append(change)

        return cls(
            old_project=_project_identification(old),
            new_project=_project_identification(new),
            removed=tuple(removed),
            added=tuple(added),
            uri_changed=tuple(uri_changed),
            display_changed=tuple(display_changed),
            candidates_changed=tuple(candidates_changed),
        )

    @property
    def metadata_changed(self) -> bool:
        """Whether the project name/version header differs."""
        return self.old_project != self.new_project

    def is_empty(self) -> bool:
        """Whether no semantic change was found."""
        return not (
            self.removed
            or self.added
            or self.uri_changed
            or self.display_changed
            or self.candidates_changed
        )

    def iter_changes(
        self, references: Iterable[ReferenceLocation] = ()
    ) -> Iterator[tuple[str, EntryChange | CandidateChange, tuple[str, ...]]]:
        """Yield ``(category, change, locations)`` for every change.

        *locations* are the collected project reference locations impacted
        by the change, sorted and deduplicated.  Unqualified references are
        attached at the name level, typed references at the
        ``(objtype, name)`` level.
        """
        typed_locations, name_locations = _index_references(references)

        def locations_for(objtype: str | None, name: str) -> tuple[str, ...]:
            locations: set[str] = set(name_locations.get(name, ()))
            if objtype is not None:
                locations.update(typed_locations.get((objtype, name), ()))
            return tuple(sorted(locations))

        for change in self.removed:
            yield REMOVED, change, locations_for(change.objtype, change.name)
        for change in self.uri_changed:
            yield URI_CHANGED, change, locations_for(change.objtype, change.name)
        for change in self.display_changed:
            yield (
                DISPLAY_CHANGED,
                change,
                locations_for(change.objtype, change.name),
            )
        for change in self.candidates_changed:
            locations = set(name_locations.get(change.name, ()))
            removed_types = set(change.old_types) - set(change.new_types)
            for objtype in removed_types:
                locations.update(typed_locations.get((objtype, change.name), ()))
            yield CANDIDATES_CHANGED, change, tuple(sorted(locations))
        for change in self.added:
            yield ADDED, change, ()


def _normalise_inventory(
    inventory: Inventory,
) -> tuple[
    dict[tuple[str, str], InventoryEntry],
    dict[str, dict[str, InventoryEntry]],
]:
    entries: dict[tuple[str, str], InventoryEntry] = {}
    by_name: dict[str, dict[str, InventoryEntry]] = {}
    for objtype, objects in inventory.items():
        for name, item in objects.items():
            entry = InventoryEntry.from_item(objtype, name, item)
            entries[objtype, name] = entry
            by_name.setdefault(name, {})[objtype] = entry
    return entries, by_name


def _project_identification(
    inventory: Inventory,
) -> tuple[str, str] | None:
    for objects in inventory.values():
        for item in objects.values():
            return item.project_name, item.project_version
    return None


def _index_references(
    references: Iterable[ReferenceLocation],
) -> tuple[
    dict[tuple[str, str], set[str]],
    dict[str, set[str]],
]:
    typed_locations: dict[tuple[str, str], set[str]] = {}
    name_locations: dict[str, set[str]] = {}
    for reference in references:
        if reference.objtype is None:
            name_locations.setdefault(reference.target, set()).add(reference.location)
        else:
            typed_locations.setdefault(
                (reference.objtype, reference.target), set()
            ).add(reference.location)
    return typed_locations, name_locations


def parse_references(text: str) -> tuple[ReferenceLocation, ...]:
    """Parse previously collected reference locations.

    Each non-empty, non-comment line is tab-separated with two or three
    columns:

    * ``location<TAB>target`` -- an unqualified (``:any:``-style) reference;
    * ``location<TAB>domain:objtype<TAB>target`` -- a typed reference;
    * ``location<TAB>domain:objtype:target`` -- equivalent two-column form.

    Lines starting with ``#`` and blank lines are ignored.
    """
    references: list[ReferenceLocation] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        columns = [column.strip() for column in line.split('\t')]
        columns = [column for column in columns if column]
        if len(columns) == 2:
            location, target = columns
            objtype: str | None = None
            if ':' in target:
                # Object types are always 'domain:objtype' while targets may
                # themselves contain colons; split off the final target.
                objtype, target = target.rsplit(':', 1)
        elif len(columns) == 3:
            location, objtype, target = columns
        else:
            msg = (
                'invalid reference line, expected 2 or 3 tab-separated '
                f'columns: {raw_line!r}'
            )
            raise ValueError(msg)
        references.append(ReferenceLocation(location, target, objtype))
    return tuple(references)


def format_diff_report(
    diff: InventoryDiff,
    *,
    old_label: str = 'old inventory',
    new_label: str = 'new inventory',
    references: Iterable[ReferenceLocation] = (),
) -> str:
    """Format an inventory diff and impacted locations as a text report."""
    lines = [
        'Intersphinx inventory comparison',
        f'  old: {old_label}{_format_project(diff.old_project)}',
        f'  new: {new_label}{_format_project(diff.new_project)}',
    ]

    if diff.metadata_changed:
        lines.extend((
            '',
            'Project information changed:',
            f'    {_format_project(diff.old_project).strip()} -> '
            f'{_format_project(diff.new_project).strip()}',
        ))

    changes = [*diff.iter_changes(references)]
    sections = (
        (
            'Objects disappeared',
            REMOVED,
            lambda change: _format_entry_change(change, show_old_uri=True),
        ),
        (
            'Target URIs changed',
            URI_CHANGED,
            lambda change: _format_entry_change(
                change,
                show_uris=True,
                show_display_names=change.display_changed,
            ),
        ),
        (
            'Display information changed',
            DISPLAY_CHANGED,
            lambda change: _format_entry_change(change, show_display_names=True),
        ),
        (
            'Same-name candidates changed',
            CANDIDATES_CHANGED,
            _format_candidate_change,
        ),
        (
            'Newly added objects',
            ADDED,
            lambda change: _format_entry_change(change, show_new_uri=True),
        ),
    )
    for title, category, formatter in sections:
        items = [item for item in changes if item[0] == category]
        if not items:
            continue
        lines.extend(('', f'{title} ({len(items)}):'))
        for _category, change, locations in items:
            lines.append(f'  {formatter(change)}')
            lines.extend(f'      referenced from: {location}' for location in locations)

    lines.extend((
        '',
        'Summary: '
        f'{len(diff.removed)} disappeared, '
        f'{len(diff.uri_changed)} target URI changed, '
        f'{len(diff.display_changed)} display information changed, '
        f'{len(diff.candidates_changed)} same-name candidate sets '
        f'changed, '
        f'{len(diff.added)} added.',
    ))
    return '\n'.join(lines)


def _format_project(project: tuple[str, str] | None) -> str:
    if project is None:
        return ''
    name, version = project
    return f'  (project {name!r}, version {version!r})'


def _format_entry_change(
    change: EntryChange,
    *,
    show_old_uri: bool = False,
    show_new_uri: bool = False,
    show_uris: bool = False,
    show_display_names: bool = False,
) -> str:
    lines = [f'{change.objtype}  {change.name}']
    if show_old_uri and change.old is not None:
        lines.append(f'    old URI: {change.old.uri}')
    if show_new_uri and change.new is not None:
        lines.append(f'    new URI: {change.new.uri}')
    if show_uris and change.old is not None and change.new is not None:
        lines.extend((
            f'    old URI: {change.old.uri}',
            f'    new URI: {change.new.uri}',
        ))
    if show_display_names and change.old is not None and change.new is not None:
        lines.extend((
            f'    old display name: {change.old.display_name}',
            f'    new display name: {change.new.display_name}',
        ))
    return '\n'.join(lines)


def _format_candidate_change(change: CandidateChange) -> str:
    old = ', '.join(f'{e.objtype} -> {e.uri}' for e in change.old)
    new = ', '.join(f'{e.objtype} -> {e.uri}' for e in change.new)
    lines = [
        change.name,
        f'    old candidates: {old}',
        f'    new candidates: {new}',
    ]
    return '\n'.join(lines)
