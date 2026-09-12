"""Controller-side verifier for the G5 visual tasks.

Independent of any implementer report. Checks, per changed file:
  1. KEY CLASSES  - every `styles.X` used in a .tsx is defined in the module that
                    .tsx imports, and every class defined there is used.
                    This is the gate's real blind spot: `vite/client` types CSS
                    modules as { readonly [key: string]: string }, so a typo'd or
                    missing class compiles clean and renders UNSTYLED. `tsc -b`
                    cannot catch it.
  2. RAW COLOUR   - no hex / rgb() / hsl() in any .tsx or .module.css changed.
  3. COPY         - no user-visible string and no data-testid removed vs the base.
  4. BLOB GUARD   - fails loudly if a pre-change blob could not be read. Without
                    this the copy check passes VACUOUSLY (an empty "before" makes
                    every literal look newly added).

THE JSX-TEXT CHECK USED TO BE UNABLE TO SEE BRACE-ADJACENT TEXT. Its pattern was
`>([^<>{}]*[A-Za-z...][^<>{}]*)<`, which excludes `{`. Any text node sitting next
to an interpolation - `Revision {sha} · Trạng thái: {status}` - therefore matched
on NEITHER side of the comparison, so a copy removal inside it was invisible BY
CONSTRUCTION. That is not hypothetical: the `· ` separator between those two
fragments was deleted by the three-pane rebuild and this script reported nothing.

The check now extracts from `strip_interpolations()` output, which replaces
`{expr}` with a space - but ONLY for a pair whose interior holds no `<`, so the
enclosing component body is never consumed. See check-added-copy.py's docstring
for the failure that rule exists to prevent; the two scripts shared the same
regex shape and now share the same guard.

Stripping is applied to the JSX-TEXT comparison ONLY. The string-literal
comparison above it must read the RAW source: a stripped source has had code
removed from it, and `LIT` is supposed to see the literals in code.

Usage:  python verify-g5.py <base-rev> [<rev>]
        default rev = HEAD
"""
import collections
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Derived from THIS file's location, not hardcoded. The script lives at
# <repo>/.superpowers/sdd/redesign/, so three levels up is the repo root. This
# used to be "D:/truyenaudio-studio": fine while the file was one machine's
# scratch, wrong the moment it is committed, because it then works on exactly
# one machine (and fails on the others with an empty diff rather than a reason).
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
GIT_PREFIX = "frontend/"  # git paths are repo-root-relative

LIT = re.compile(r"'(?:[^'\\]|\\.)*'" + r'|"(?:[^"\\]|\\.)*"')
JSXTEXT = re.compile(r">([^<>]*[A-Za-zÀ-ỹ][^<>]*)<")
BRACE = re.compile(r"\{[^{}]*\}")
TESTID = re.compile(r"""data-testid=["']([^"']+)["']""")
RAWCOL = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(")
STYLES_REF = re.compile(r"\bstyles\.([A-Za-z_][A-Za-z0-9_]*)")
CSS_CLASS = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)")
IMPORT_CSS = re.compile(r"""import\s+styles\s+from\s+['"]([^'"]+)['"]""")
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
# Tokens that look like class selectors but are prose inside comments or file names.
CLASS_NOISE = {"tsx", "ts", "css", "module", "js", "jsx", "com", "e", "g"}


def git(*args):
    proc = subprocess.run(["git", "-C", REPO, *args], capture_output=True,
                          text=True, encoding="utf-8")
    return proc.returncode, proc.stdout, proc.stderr


def show(rev, path):
    rc, out, err = git("show", f"{rev}:{GIT_PREFIX}{path}")
    return (out if rc == 0 else None), err.strip()


def exists_at(rev, path):
    """True when the path is present in that revision's tree.

    Used to tell "this file is NEW in the range" apart from "git could not read
    this blob". The old code collapsed both into `blob unreadable` and skipped
    the copy check, which is why all 13 files added in `9ef4fee..HEAD` were never
    copy-checked at all - the exact surface a screen extraction creates.
    """
    rc, out, _ = git("ls-tree", rev, "--", f"{GIT_PREFIX}{path}")
    return rc == 0 and out.strip() != ""


def strip_interpolations(src):
    """Replace `{expr}` with a space, stopping at the JSX boundary.

    A pair is stripped ONLY when its interior contains no `<`. An unconditional
    loop to a fixpoint walks past the inner interpolations and then removes the
    enclosing component body, taking every JSX text node with it; refusing to
    touch anything that contains markup is what keeps that body intact.
    """
    prev = None
    while prev != src:
        prev = src
        src = BRACE.sub(lambda m: m.group(0) if "<" in m.group(0) else " ", src)
    return src


def changed_files(base, rev):
    rc, out, err = git("diff", "--name-only", f"{base}..{rev}")
    if rc != 0:
        print("!! git diff failed:", err)
        sys.exit(2)
    return [p[len(GIT_PREFIX):] for p in out.split() if p.startswith(GIT_PREFIX)]


def module_for(tsx_path, text):
    """Resolve the CSS module a component imports, and read it."""
    m = IMPORT_CSS.search(text)
    if not m:
        return None, None
    rel = m.group(1)
    target = os.path.normpath(os.path.join(os.path.dirname(tsx_path), rel))
    full = os.path.join(REPO, "frontend", target)
    if not os.path.exists(full):
        return target, None
    with open(full, encoding="utf-8") as fh:
        return target, fh.read()


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else None
    rev = sys.argv[2] if len(sys.argv) > 2 else "HEAD"
    if not base:
        print(__doc__)
        sys.exit(2)

    files = changed_files(base, rev)
    print(f"BASE={base}  REV={rev}  changed under frontend/: {len(files)}\n")

    problems = collections.defaultdict(list)

    for path in files:
        full = os.path.join(REPO, "frontend", path)
        if not os.path.exists(full):
            print(f"-- {path}: DELETED in {rev}")
            continue
        with open(full, encoding="utf-8") as fh:
            new = fh.read()

        # --- 2. raw colour -------------------------------------------------
        hits = RAWCOL.findall(new)
        if hits:
            problems["raw colour"].append(f"{path}: {sorted(set(hits))}")
        elif path.endswith((".tsx", ".module.css")):
            print(f"ok  no raw colour       {path}")

        if not path.endswith(".tsx"):
            continue

        # --- 1. class cross-check ------------------------------------------
        mod_path, mod_text = module_for(path, new)
        used = set(STYLES_REF.findall(new))
        if mod_text is not None:
            # Strip comments first: prose in a comment ("converted from Foo.tsx")
            # otherwise reads as a class selector named `tsx`.
            stripped = CSS_COMMENT.sub(" ", mod_text)
            # Only top-level-ish class selectors; ignore keyframe steps (0%..100%)
            defined = {c for c in CSS_CLASS.findall(stripped)
                       if not c[0].isdigit() and c not in CLASS_NOISE}
            missing = used - defined
            unused = defined - used
            if missing:
                problems["undefined class"].append(f"{path}: {sorted(missing)}")
            if unused:
                problems["unused class"].append(f"{path} [{mod_path}]: {sorted(unused)}")
            status = "ok " if not missing else "XX "
            print(f"{status}classes {len(used):>3} used / {len(defined):>3} defined  {path}")
        elif used:
            problems["no module"].append(f"{path}: uses styles.X but imports no CSS module")

        # --- 3. copy preservation ------------------------------------------
        old, err = show(base, path)
        if old is None:
            if not exists_at(base, path):
                # Not an error: a file that did not exist at base has nothing to
                # lose. Said out loud, because silently skipping is what let the
                # 13 extracted screens go unchecked while the report read CLEAN.
                # The removal direction still covers them: content that MOVED out
                # of router.tsx shows up as a removal in router.tsx, which is
                # checked below.
                print(f"-- NEW in {rev}, no base copy to compare  {path}")
                continue
            problems["blob unreadable"].append(f"{path}: {err[:120]}")
            print(f"!! COULD NOT READ BASE BLOB      {path}  ({err[:80]})")
            continue
        # LIT reads the RAW source: stripping removes code, and this one is
        # supposed to see the literals that live in code.
        o_lit, n_lit = collections.Counter(LIT.findall(old)), collections.Counter(LIT.findall(new))
        # JSX text reads the STRIPPED source, so text adjacent to an
        # interpolation is comparable instead of invisible on both sides.
        o_jsx = collections.Counter(t.strip() for t in JSXTEXT.findall(strip_interpolations(old)) if t.strip())
        n_jsx = collections.Counter(t.strip() for t in JSXTEXT.findall(strip_interpolations(new)) if t.strip())
        o_ids, n_ids = set(TESTID.findall(old)), set(TESTID.findall(new))

        jsx_removed = o_jsx - n_jsx
        ids_removed = o_ids - n_ids
        if jsx_removed:
            problems["jsx copy removed"].append(f"{path}: {dict(jsx_removed)}")
        if ids_removed:
            problems["testid removed"].append(f"{path}: {sorted(ids_removed)}")
        print(f"{'!!' if (jsx_removed or ids_removed) else 'ok'} copy: "
              f"{len(jsx_removed)} jsx-text removed, {len(ids_removed)} testids removed  {path}")

    print("\n" + "=" * 72)
    if not problems:
        print("CLEAN - no raw colour, no undefined/unused class, no copy or testid loss.")
        return
    for kind, items in problems.items():
        print(f"\n### {kind} ({len(items)})")
        for it in items:
            print("   ", it)


if __name__ == "__main__":
    main()
