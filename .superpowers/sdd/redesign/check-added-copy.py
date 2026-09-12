"""Find JSX text nodes ADDED between two revisions of the changed files.

verify-g5.py answers "was any user-visible copy REMOVED?", which is the direction
that breaks existing tests. The other direction - copy that did not exist before -
is the one the frozen-copy rule also forbids, and the one nothing else checks.

Blind spots, both producing FALSE POSITIVES only (never a missed addition):

  * interpolated text - `Doan #{index + 1}` - is stripped, so a text node
    containing `{` splits and can be reported as new. Strip `{...}` before
    extracting, which is what this script does.
  * `>text<` spanning an arrow function's `>` to a later `<` captures source code.
    Anything reported should be read in context before being believed.

WHY strip_interpolations() IS WRITTEN THE WAY IT IS - this script used to be
useless and reported "0 added" for almost the whole repo, which read as CLEAN.
It stripped braces with `while prev != body: body = BRACE.sub(" ", body)`, an
unconditional loop to a fixpoint. That is fine for the interpolation it was
written for, but it does not STOP there: once the inner JSX braces are gone, the
next pass matches the ENCLOSING COMPONENT BODY and deletes the JSX along with it.
Measured on ThemeChangeNotice.tsx, which has two visible string literals in
source: texts() returned []. A census over frontend/src found 63 of 69 non-test
.tsx files yielding ZERO text nodes. The old docstring claimed the blind spots
were "FALSE POSITIVES only (never a missed addition)" - the exact opposite of
what this did, and the reason the branch's copy freeze had no real backing.

The fix is a refusal, not a cleverer regex: a brace pair whose interior contains
`<` is JSX markup or a function body that contains some, so it is LEFT ALONE.
That is what stops the loop at the JSX boundary instead of eating through it.
Consequence, stated honestly: an interpolation that itself contains JSX
(`{cond ? <b/> : null}`) is not stripped, so the text around it can split into
fragments and be reported. That is a false positive a human resolves by reading
context - the direction this script is allowed to be wrong in.

Usage:  python check-added-copy.py <old-rev> <new-rev>
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
JSXTEXT = re.compile(r">([^<>]*?[A-Za-z\u00C0-\u1EF9][^<>]*?)<")
BRACE = re.compile(r"\{[^{}]*\}")


def git(*args):
    p = subprocess.run(["git", "-C", REPO, *args], capture_output=True,
                       text=True, encoding="utf-8")
    return p.returncode, p.stdout


def changed_files(old, new):
    rc, out = git("diff", "--name-only", old, new, "--", "frontend/src")
    if rc != 0:
        print(f"!! cannot diff {old}..{new}")
        sys.exit(2)
    return [p for p in out.split() if p.endswith(".tsx")]


def read(rev, path):
    rc, out = git("show", f"{rev}:{path}")
    return out if rc == 0 else ""


def strip_interpolations(src):
    """Replace `{expr}` with a space, stopping at the JSX boundary.

    A pair is stripped ONLY when its interior contains no `<`. See the module
    docstring: an unconditional loop to a fixpoint walks past the inner
    interpolations and then removes the enclosing component body, taking all the
    JSX text with it. Refusing to touch anything that contains markup is what
    keeps the enclosing body - which always contains markup - intact.
    """
    prev = None
    while prev != src:
        prev = src
        src = BRACE.sub(lambda m: m.group(0) if "<" in m.group(0) else " ", src)
    return src


def texts(body):
    stripped = strip_interpolations(body)
    out = []
    for m in JSXTEXT.findall(stripped):
        t = " ".join(m.split())
        if t and not t.startswith("//") and not t.startswith("*"):
            out.append(t)
    return out


def main():
    old, new = sys.argv[1], sys.argv[2]
    files = changed_files(old, new)
    print(f"comparing {old}..{new}   tsx files: {len(files)}\n")

    total = 0
    for path in files:
        before = texts(read(old, path))
        after = texts(read(new, path))
        # Multiset difference: a string that existed N times and now exists M>N
        # is NOT new copy. "more of it" and "added it" are different questions.
        added = collections.Counter(after) - collections.Counter(before)
        # Drop pure-propagation noise: single words that were already present.
        if added:
            real = {k: v for k, v in added.items()
                    if k not in before and len(k) > 1}
            if real:
                total += len(real)
                print(f"### {path}")
                for k, v in sorted(real.items()):
                    print(f"      +{v}  {k!r}")
    print(f"\nfiles with genuinely-new JSX text: {total}")


if __name__ == "__main__":
    main()
