# -*- coding: UTF-8 -*-

# Copyright (c) 2020 The ungoogled-chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE.ungoogled_chromium file.
"""
Tests for patches/helium/core/sync/engine-interface.patch

This patch is not compiled as part of this repository's own test suite (it is
only applied to a full Chromium checkout during a real build), so these tests
validate it the same way devutils/validate_patches.py does: by parsing it as a
unified diff with the vendored "unidiff" library, and by checking that its
hunks are internally consistent unified-diff syntax. They also assert on the
semantic content the patch is supposed to introduce (the CustomSyncBackend
interface, the Helium shim, and the fallback wiring in
SyncEngineBackend::DoInitialize).
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'third_party'))
import unidiff

sys.path.pop(0)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'utils'))
from _common import ENCODING

sys.path.pop(0)

_PATCH_PATH = (Path(__file__).resolve().parent.parent.parent / 'patches' / 'helium' / 'core' /
               'sync' / 'engine-interface.patch')

# Files this patch creates from scratch ("--- /dev/null")
_NEW_FILES = (
    'components/sync/engine/custom_sync_backend.h',
    'components/sync/service/helium_sync_backend.h',
    'components/sync/service/helium_sync_backend.cc',
)

# Files this patch modifies in-place
_MODIFIED_FILES = (
    'components/sync/engine/sync_engine.cc',
    'components/sync/service/glue/sync_engine_backend.cc',
)

_ALL_FILES = _NEW_FILES + _MODIFIED_FILES

# Matches a "@@ -old_start,old_count +new_start,new_count @@" hunk header,
# tolerating stray leading '+'/'-'/' ' characters that may have leaked in
# from an improperly-applied nested diff.
_HUNK_HEADER_RE = re.compile(r'^[ +\-]*@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')

# Matches a "--- ..." or "+++ ..." file header line, with the same tolerance
# for stray leading characters as _HUNK_HEADER_RE.
_FILE_MARKER_RE = re.compile(r'^[ +\-]*(?:---|\+\+\+) ')

# A file header or hunk header line that itself begins with exactly one
# leftover '+' or '-' character is a strong signal that content from an outer
# "diff of the patch file" was pasted into the patch instead of being
# resolved into plain unified-diff syntax.
_LEAKED_DIFF_MARKER_RE = re.compile(r'^[+\-](---|\+\+\+|@@) ')


@pytest.fixture(scope='module')
def patch_text():
    """Returns the raw text content of the patch file"""
    return _PATCH_PATH.read_text(encoding=ENCODING)


@pytest.fixture(scope='module')
def patch_lines(patch_text):
    """Returns the patch file content split into individual lines, without line endings"""
    return patch_text.splitlines()


def _section_start(lines, path):
    """Returns the index of the "--- ..." line that precedes "+++ b/<path>" """
    marker = f'+++ b/{path}'
    for index, line in enumerate(lines):
        if line.rstrip('\n').endswith(marker):
            return index - 1
    raise AssertionError(f'Could not locate a "{marker}" file header in the patch')


def _section_end(lines, path, start):
    """
    Returns the index at which the file section for path (starting at index
    start) ends, i.e. the start of the next known file section, or len(lines)
    if it is the last section in the patch.
    """
    next_starts = []
    for other_path in _ALL_FILES:
        if other_path == path:
            continue
        try:
            other_start = _section_start(lines, other_path)
        except AssertionError:
            continue
        if other_start > start:
            next_starts.append(other_start)
    return min(next_starts) if next_starts else len(lines)


def _get_section_text(lines, path):
    """Returns the substring of the patch (as text) covering just path's file section"""
    start = _section_start(lines, path)
    end = _section_end(lines, path, start)
    return ''.join(line if line.endswith('\n') else line + '\n' for line in lines[start:end])


def _find_hunk_line_count_mismatches(lines):
    """
    Returns a list of (line_index, header_text, declared_counts, actual_counts)
    for every "@@ ... @@" hunk header whose declared (old_count, new_count)
    does not match the number of context/removed/added lines actually found
    between it and the next hunk header, file header, or end of file.

    This does not depend on unidiff and therefore gives a precise diagnostic
    independent of that library's specific parsing/error behavior.
    """
    boundary_indices = set()
    header_indices = []
    for index, line in enumerate(lines):
        if _HUNK_HEADER_RE.match(line):
            header_indices.append(index)
            boundary_indices.add(index)
        elif _FILE_MARKER_RE.match(line):
            boundary_indices.add(index)
    boundary_indices.add(len(lines))
    sorted_boundaries = sorted(boundary_indices)

    mismatches = []
    for header_index in header_indices:
        match = _HUNK_HEADER_RE.match(lines[header_index])
        declared_old = int(match.group(2)) if match.group(2) is not None else 1
        declared_new = int(match.group(4)) if match.group(4) is not None else 1
        next_boundary = next(b for b in sorted_boundaries if b > header_index)
        body = lines[header_index + 1:next_boundary]
        added = sum(1 for line in body if line.startswith('+'))
        removed = sum(1 for line in body if line.startswith('-'))
        context = len(body) - added - removed
        actual = (context + removed, context + added)
        declared = (declared_old, declared_new)
        if actual != declared:
            mismatches.append((header_index, lines[header_index], declared, actual))
    return mismatches


# ---------------------------------------------------------------------------
# Basic file sanity
# ---------------------------------------------------------------------------


def test_patch_file_exists():
    """The patch file under test must exist at the expected path"""
    assert _PATCH_PATH.is_file()


def test_patch_file_is_not_empty(patch_text):
    assert patch_text.strip()


def test_patch_file_ends_with_newline(patch_text):
    """
    devutils/validate_patches.py::_load_all_patches() treats a missing
    trailing newline as a validation failure.
    """
    assert patch_text.endswith('\n')


def test_patch_declares_expected_new_files(patch_text):
    """Every file this patch is meant to create has a "/dev/null" source and a target header"""
    for path in _NEW_FILES:
        assert f'b/{path}' in patch_text, f'Missing target header for new file: {path}'


def test_patch_declares_expected_modified_files(patch_text):
    """Every file this patch is meant to modify has both "a/<path>" and "b/<path>" headers"""
    for path in _MODIFIED_FILES:
        assert f'a/{path}' in patch_text, f'Missing source header for modified file: {path}'
        assert f'b/{path}' in patch_text, f'Missing target header for modified file: {path}'


# ---------------------------------------------------------------------------
# Structural validity (mirrors what devutils/validate_patches.py requires)
# ---------------------------------------------------------------------------


def test_all_hunks_have_consistent_line_counts(patch_lines):
    """
    Regression test: every "@@ -old_start,old_count +new_start,new_count @@"
    hunk header must be followed by exactly old_count old (context+removed)
    lines and new_count new (context+added) lines before the next hunk, file
    header, or end of file. A mismatch means the diff is not valid unified
    diff syntax and will not be parseable by "patch", "git apply", or
    unidiff (as used by devutils/validate_patches.py).
    """
    mismatches = _find_hunk_line_count_mismatches(patch_lines)
    assert not mismatches, (
        'Found hunk(s) with inconsistent line counts (malformed unified diff syntax): '
        f'{mismatches}')


def test_no_leaked_nested_diff_markers(patch_lines):
    """
    Regression/negative test for a specific corruption pattern: a file or
    hunk header ("---", "+++", "@@") that itself begins with exactly one
    leftover '+' or '-' character (e.g. "+--- /dev/null", "++++ b/foo.h",
    "+@@ -0,0 +1,5 @@", "-@@ -1,2 +1,2 @@"). This pattern indicates that
    content from an outer "diff of the patch file" was pasted directly into
    the patch instead of being resolved into plain unified-diff syntax.
    """
    leaked = [(index, line) for index, line in enumerate(patch_lines)
             if _LEAKED_DIFF_MARKER_RE.match(line)]
    assert not leaked, f'Found leaked/nested diff markers in patch body: {leaked}'


def test_patch_is_parseable_by_unidiff(patch_text):
    """
    devutils/validate_patches.py loads every patch with
    unidiff.PatchSet.from_filename() before it can be checked against the
    Chromium source tree, so the patch must be valid, parseable unified-diff
    syntax covering exactly the files it is meant to touch.
    """
    patch_set = unidiff.PatchSet(patch_text)
    patched_paths = {patched_file.path for patched_file in patch_set}
    for path in _ALL_FILES:
        assert path in patched_paths, f'unidiff did not recognize a diff for: {path}'


# ---------------------------------------------------------------------------
# Per-file hunk metadata (isolated from the rest of the patch so that a
# malformed hunk elsewhere in the file does not prevent testing a well-formed
# one)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('path', _NEW_FILES)
def test_new_file_hunk_is_a_single_added_file_hunk(patch_lines, path):
    """
    Each newly-added file must be represented by exactly one hunk, adding
    lines starting at line 1 with nothing removed.
    """
    section_text = _get_section_text(patch_lines, path)
    patch_set = unidiff.PatchSet(section_text)
    assert len(patch_set) == 1, f'Expected exactly one diff entry for {path}'
    patched_file = patch_set[0]
    assert patched_file.path == path
    assert patched_file.is_added_file
    assert len(patched_file) == 1, f'Expected exactly one hunk for {path}'
    assert patched_file[0].removed == 0
    assert patched_file[0].target_start == 1


# ---------------------------------------------------------------------------
# Content correctness
# ---------------------------------------------------------------------------


def test_custom_sync_backend_header_content(patch_lines):
    """
    Verifies the reconstructed content of the newly added
    components/sync/engine/custom_sync_backend.h defines the interface that
    the rest of this patch (and the Helium shim) depends on.
    """
    path = 'components/sync/engine/custom_sync_backend.h'
    section_text = _get_section_text(patch_lines, path)
    patched_file = unidiff.PatchSet(section_text)[0]
    added_lines = [line.value.rstrip('\n') for line in patched_file[0] if line.is_added]
    reconstructed = '\n'.join(added_lines)

    assert '#ifndef COMPONENTS_SYNC_ENGINE_CUSTOM_SYNC_BACKEND_H_' in reconstructed
    assert '#define COMPONENTS_SYNC_ENGINE_CUSTOM_SYNC_BACKEND_H_' in reconstructed
    assert '#endif  // COMPONENTS_SYNC_ENGINE_CUSTOM_SYNC_BACKEND_H_' in reconstructed
    assert 'namespace syncer {' in reconstructed
    assert 'class CustomSyncBackend {' in reconstructed
    assert 'CustomSyncBackend(const CustomSyncBackend&) = delete;' in reconstructed
    assert 'CustomSyncBackend& operator=(const CustomSyncBackend&) = delete;' in reconstructed
    assert 'virtual ~CustomSyncBackend() = default;' in reconstructed
    assert ('virtual std::unique_ptr<ServerConnectionManager> CreateConnectionManager('
            in reconstructed)
    assert 'CancelationSignal* cancelation_signal) = 0;' in reconstructed
    assert 'virtual std::string GetDebugName() const = 0;' in reconstructed
    assert 'GPL-3.0 license' in reconstructed


def test_helium_sync_backend_header_declares_factory_function(patch_text):
    """
    components/sync/service/helium_sync_backend.h must declare a factory
    function returning a std::unique_ptr<CustomSyncBackend>, and must include
    the base class's header.
    """
    assert '#include "components/sync/engine/custom_sync_backend.h"' in patch_text
    assert ('std::unique_ptr<CustomSyncBackend> CreateHeliumCustomSyncBackend();' in patch_text)


def test_helium_sync_backend_impl_returns_nullptr_by_default(patch_text):
    """
    The default implementation of CreateHeliumCustomSyncBackend() must
    return nullptr so that builds succeed without a real Helium provider
    configured.
    """
    impl_signature = 'std::unique_ptr<CustomSyncBackend> CreateHeliumCustomSyncBackend() {'
    assert impl_signature in patch_text
    impl_index = patch_text.index(impl_signature)
    closing_brace_index = patch_text.index('}', impl_index)
    body = patch_text[impl_index:closing_brace_index]
    assert 'return nullptr;' in body


def test_sync_engine_header_forward_declares_custom_sync_backend(patch_text):
    """sync_engine.h must forward-declare CustomSyncBackend so it can be referenced by pointer"""
    assert 'class CustomSyncBackend;' in patch_text


def test_sync_engine_backend_includes_helium_shim_header(patch_text):
    """sync_engine_backend.cc must include the new Helium shim header"""
    assert '#include "components/sync/service/helium_sync_backend.h"' in patch_text


def test_sync_engine_backend_prefers_explicit_backend_over_helium_shim(patch_text):
    """
    The wiring added to SyncEngineBackend::DoInitialize() must prefer an
    explicitly-configured params.custom_sync_backend, and only fall back to
    syncer::CreateHeliumCustomSyncBackend() when none was provided.
    """
    assert 'if (params.custom_sync_backend) {' in patch_text
    assert 'args.custom_sync_backend = std::move(params.custom_sync_backend);' in patch_text
    assert '} else {' in patch_text
    assert 'args.custom_sync_backend = syncer::CreateHeliumCustomSyncBackend();' in patch_text

    # Search starting from the "if" so that this checks the final, intended
    # code ordering rather than incidentally matching an unrelated earlier
    # occurrence of the same statement text elsewhere in the diff.
    if_index = patch_text.index('if (params.custom_sync_backend) {')
    move_index = patch_text.index(
        'args.custom_sync_backend = std::move(params.custom_sync_backend);', if_index)
    else_index = patch_text.index('} else {', if_index)
    fallback_index = patch_text.index(
        'args.custom_sync_backend = syncer::CreateHeliumCustomSyncBackend();', if_index)

    # The explicit-backend branch must come before the "else" branch, and the
    # fallback call must be inside the "else" branch.
    assert if_index < move_index < else_index < fallback_index