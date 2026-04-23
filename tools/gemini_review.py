#!/usr/bin/env python3
"""
gemini_review.py - Third-party code review using Gemini Robotics-ER 1.6.

Reviews SlyLED source files and writes a markdown report.

Usage:
    python tools/gemini_review.py --key YOUR_API_KEY [--files file1 file2 ...]
    python tools/gemini_review.py --key YOUR_API_KEY --all
    python tools/gemini_review.py --key YOUR_API_KEY --gyro
    python tools/gemini_review.py --key YOUR_API_KEY --design

Examples:
    python tools/gemini_review.py --key AIza... --gyro
    python tools/gemini_review.py --key AIza... --design   # review calibration v2 design (#488)
    python tools/gemini_review.py --key AIza... --files desktop/shared/mover_control.py main/GyroUdp.cpp
    python tools/gemini_review.py --key YOUR_API_KEY --all --out review.md
"""

import argparse
import os
import subprocess
import sys
import datetime

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("ERROR: google-genai not installed. Run: pip install google-genai")
    sys.exit(1)

# ---------------------------------------------------------------------------
# File groups
# ---------------------------------------------------------------------------

ROOT = os.path.join(os.path.dirname(__file__), '..')

GYRO_FILES = [
    'main/Protocol.h',
    'main/GyroBoard.h',
    'main/GyroIMU.h',
    'main/GyroIMU.cpp',
    'main/GyroDisplay.h',
    'main/GyroDisplay.cpp',
    'main/GyroTouch.h',
    'main/GyroTouch.cpp',
    'main/GyroUdp.h',
    'main/GyroUdp.cpp',
    'main/GyroUI.h',
    'main/GyroUI.cpp',
    'main/GyroLogo.h',
    'desktop/shared/remote_math.py',
    'desktop/shared/remote_orientation.py',
    'desktop/shared/mover_control.py',
    'desktop/shared/spatial_engine.py',
    'tests/test_gyro_protocol.py',
    'tests/test_gyro_api.py',
    'tests/test_remote_math.py',
    'tests/test_remote_orientation.py',
    'tests/test_mover_control.py',
    'tests/emulated_gyro.py',
]

SPA_FILES = [
    'desktop/shared/spa/index.html',
]

CAMERA_FILES = [
    'firmware/orangepi/camera_server.py',
    'firmware/orangepi/detector.py',
    'firmware/orangepi/tracker.py',
    'firmware/orangepi/beam_detector.py',
    'firmware/orangepi/depth_estimator.py',
    'desktop/shared/cv_engine.py',
    'desktop/shared/stereo_engine.py',
    'desktop/shared/structured_light.py',
    'desktop/shared/surface_analyzer.py',
    'desktop/shared/space_mapper.py',
]

CALIBRATION_DESIGN_FILES = [
    'docs/mover-calibration-v2.md',
    'desktop/shared/mover_calibrator.py',
    'desktop/shared/dmx_profiles.py',
    'desktop/shared/surface_analyzer.py',
    'desktop/shared/space_mapper.py',
]

SERVER_FILES = [
    'desktop/shared/parent_server.py',
    'desktop/shared/main.py',
    'desktop/shared/bake_engine.py',
    'desktop/shared/show_generator.py',
    'desktop/shared/dmx_artnet.py',
    'desktop/shared/dmx_sacn.py',
    'desktop/shared/dmx_universe.py',
    'desktop/shared/dmx_profiles.py',
    'desktop/shared/ofl_importer.py',
    'desktop/shared/mover_calibrator.py',
    'desktop/shared/mover_control.py',
    'desktop/shared/wled_bridge.py',
    'desktop/shared/community_client.py',
    'desktop/shared/firmware_manager.py',
]

FIRMWARE_FILES = [
    'main/BoardConfig.h',
    'main/Globals.h',
    'main/Globals.cpp',
    'main/Protocol.h',
    'main/version.h',
    'main/NetUtils.h',
    'main/NetUtils.cpp',
    'main/HttpUtils.h',
    'main/HttpUtils.cpp',
    'main/JsonUtils.h',
    'main/JsonUtils.cpp',
    'main/OtaUpdate.h',
    'main/OtaUpdate.cpp',
    'main/UdpCommon.h',
    'main/UdpCommon.cpp',
    'main/ArtNetRecv.h',
    'main/ArtNetRecv.cpp',
    'main/Child.h',
    'main/Child.cpp',
    'main/ChildLED.h',
    'main/ChildLED.cpp',
    'main/GigaLED.h',
    'main/GigaLED.cpp',
    'main/DmxBridge.h',
    'main/DmxBridge.cpp',
    'main/Parent.h',
    'main/Parent.cpp',
    'main/main.ino',
]

# Groups used by --all (each becomes its own batch set)
ALL_GROUPS = [
    ('gyro',     GYRO_FILES),
    ('spa',      SPA_FILES),
    ('camera',   CAMERA_FILES),
    ('server',   SERVER_FILES),
    ('firmware', FIRMWARE_FILES),
]

REVIEW_PROMPT = """\
You are an expert code reviewer with deep knowledge of C++, Python, embedded \
systems, point clouds, real space, DMX/ArtNet lighting control, and UDP networking protocols.


Please review the following source files from the SlyLED lighting control \
system. This is a third-party independent review — be thorough, critical, and \
constructive.

For each file, identify:
1. **Bugs** — logic errors, off-by-one errors, race conditions, undefined behaviour
2. **Security issues** — injection, unvalidated input, buffer overflows
3. **Robustness** — unhandled edge cases, missing error handling
4. **Performance** — unnecessary work, inefficient patterns
5. **Code quality** — readability, naming, structure, dead code
6. **Universal Coordinate Correctness** - many constructs use virtual space, camera vision to identify space and moving heads that point beams of light in that space. Bugs with inverted fixtures and mixing up Y/Z space when rendering may be an issue

Conclude with an overall summary and a prioritised list of the top issues \
to fix (P1 = must fix, P2 = should fix, P3 = nice to have).

Format the output as Markdown with a section per file, then the summary.

--- SOURCE FILES ---

{source}
"""

DESIGN_REVIEW_PROMPT = """\
You are a senior software architect and lighting systems engineer with deep \
knowledge of DMX/ArtNet, moving head fixture control, 3D stage visualization, \
inverse kinematics, camera-based calibration, and professional lighting consoles \
(grandMA3, ETC Eos, Avolites, Chamsys, Depence).

You are reviewing a **design document** for a moving head calibration system \
(SlyLED — an open-source LED/DMX lighting control platform). The design proposes \
replacing a linear affine fit with a parametric kinematic model calibrated via \
Levenberg-Marquardt, with both camera-assisted and manual workflows.

The current implementation file is also included so you can assess feasibility \
against the existing codebase.

Please review the design for:

1. **Mathematical correctness** — Are the rotation matrices, forward/inverse \
kinematics, and IK formulas correct? Are the singularity guards adequate? \
Is the LM solver configuration (residuals, Jacobian, convergence criteria) sound?

2. **Competitor analysis accuracy** — Is the competitor survey factually correct? \
Are there claims about grandMA3, ETC Eos, Avolites, Chamsys, Depence, or Hog 4 \
that are wrong or misleading? Are there competitors or approaches that were missed?

3. **Architecture and feasibility** — Is the migration plan from v1 to v2 safe? \
Are there race conditions, data loss risks, or backward compatibility issues? \
Can the existing codebase (mover_calibrator.py) support this design without a rewrite?

4. **Calibration workflow** — Is the camera-assisted workflow realistic? Are the \
settle times, beam detection thresholds, and convergence criteria appropriate for \
real moving head fixtures? Are there edge cases the design misses (e.g., ambient \
light interference, multi-beam fixtures, fixtures with non-standard pan/tilt mapping)?

5. **Home position design** — Is formalizing the existing aim vector as a home \
position preset the right approach? Are there edge cases (uncalibrated fixture, \
no rotation set, fixture removed and re-added)?

6. **Point cloud integration** — Is using the space_mapper/surface_analyzer \
point cloud as the primary geometry source sound? Are there accuracy concerns \
with monocular depth estimation for calibration target placement?

7. **Gaps and risks** — What does the design miss? What are the biggest risks \
to successful implementation? What would you change?

Conclude with:
- **Overall assessment** (ready to implement / needs revision / fundamentally flawed)
- **Top 5 risks** (prioritized)
- **Top 5 recommendations** (what to change or add before implementation)

Format as Markdown.

--- DESIGN DOCUMENT AND SOURCE FILES ---

{source}
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GIT_REF = 'origin/main'


def fetch_from_github():
    """Fetch latest commits from origin so GIT_REF is current."""
    print(f"Fetching latest from GitHub...")
    result = subprocess.run(
        ['git', 'fetch', 'origin'],
        capture_output=True, text=True, cwd=ROOT,
    )
    if result.returncode == 0:
        print(f"  [git] Fetched origin. Reading files from {GIT_REF}.")
    else:
        print(f"  [warn] git fetch failed: {result.stderr.strip()}")
        print(f"  [warn] Falling back to local working tree.")
        return False
    return True


def load_files(paths, use_git=False):
    """Return list of (filename, formatted_block) tuples.

    If use_git=True, read each file from GIT_REF (origin/main) via git show,
    so the review targets committed GitHub code rather than the working tree.
    """
    blocks = []
    for p in paths:
        content = None
        if use_git:
            result = subprocess.run(
                ['git', 'show', f'{GIT_REF}:{p}'],
                capture_output=True, text=True, cwd=ROOT,
            )
            if result.returncode == 0:
                content = result.stdout
                source_label = GIT_REF
            else:
                print(f"  [skip] {p} — not in {GIT_REF}")
                continue
        else:
            full = os.path.join(ROOT, p)
            if not os.path.exists(full):
                print(f"  [skip] {p} — not found")
                continue
            try:
                with open(full, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                source_label = 'local'
            except Exception as e:
                print(f"  [skip] {p} — {e}")
                continue

        block = f"### {p}\n```\n{content}\n```\n"
        blocks.append((p, block))
        print(f"  [+] {p} ({len(content):,} chars) [{source_label}]")
    return blocks


# ~280k chars ≈ 93k tokens at 3 chars/token — safe under 131k limit with prompt+output headroom
MAX_SOURCE_CHARS = 280_000


def _call_api(client, source_text, batch_num, total_batches, prompt_template=None):
    label = f" (batch {batch_num}/{total_batches})" if total_batches > 1 else ""
    template = prompt_template or REVIEW_PROMPT
    prompt = template.format(source=source_text)
    print(f"\nSending {len(prompt):,} chars{label} to gemini-robotics-er-1.6-preview ...")
    response = client.models.generate_content(
        model='gemini-robotics-er-1.6-preview',
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=8192,
        ),
    )
    return response.text


def _file_to_parts(name, block):
    """If a single file's block exceeds MAX_SOURCE_CHARS, split it into named parts."""
    if len(block) <= MAX_SOURCE_CHARS:
        return [(name, block)]
    parts = []
    # Split on line boundaries to avoid breaking mid-token
    lines = block.splitlines(keepends=True)
    chunk_lines = []
    chunk_size = 0
    part_num = 1
    for line in lines:
        if chunk_size + len(line) > MAX_SOURCE_CHARS and chunk_lines:
            chunk_text = ''.join(chunk_lines)
            parts.append((f"{name} [part {part_num}]", chunk_text))
            part_num += 1
            chunk_lines = []
            chunk_size = 0
        chunk_lines.append(line)
        chunk_size += len(line)
    if chunk_lines:
        parts.append((f"{name} [part {part_num}]", ''.join(chunk_lines)))
    return parts


def _split_into_batches(file_blocks):
    """Split list of (filename, content_block) into char-safe batches.
    Files larger than MAX_SOURCE_CHARS are split into parts first."""
    # Expand any oversized files into parts
    expanded = []
    for name, block in file_blocks:
        expanded.extend(_file_to_parts(name, block))

    batches = []
    current = []
    current_size = 0
    for name, block in expanded:
        size = len(block)
        if current_size + size > MAX_SOURCE_CHARS and current:
            batches.append(current)
            current = []
            current_size = 0
        current.append((name, block))
        current_size += size
    if current:
        batches.append(current)
    return batches


def review_group(client, group_label, file_blocks, all_reviews, prompt_template=None):
    """Review one named group, splitting into sub-batches if needed.
    Appends section(s) to all_reviews list."""
    batches = _split_into_batches(file_blocks)
    for i, batch in enumerate(batches, 1):
        file_names = [name for name, _ in batch]
        source_text = '\n'.join(block for _, block in batch)
        sub = f" (part {i}/{len(batches)})" if len(batches) > 1 else ""
        print(f"\n[{group_label}{sub}] {len(file_names)} file(s): {', '.join(file_names)}")
        review_text = _call_api(client, source_text, i, len(batches), prompt_template)
        heading = f"## Group: {group_label}{sub} — {', '.join(file_names)}"
        all_reviews.append(f"{heading}\n\n{review_text}")


def run_review(api_key, groups, out_path, prompt_template=None):
    """groups: list of (label, file_blocks) tuples — each gets its own batch."""
    client = genai.Client(api_key=api_key)

    total_files = sum(len(fb) for _, fb in groups)
    review_type = "Design Review" if prompt_template == DESIGN_REVIEW_PROMPT else "Code Review"
    header = (
        f"# SlyLED {review_type}\n"
        f"**Model:** gemini-robotics-er-1.6-preview  \n"
        f"**Date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}  \n"
        f"**Files reviewed:** {total_files}  \n"
        f"**Groups:** {', '.join(label for label, _ in groups)}\n\n---\n\n"
    )

    all_reviews = []
    for label, file_blocks in groups:
        if not file_blocks:
            print(f"\n[{label}] No files loaded — skipping.")
            continue
        review_group(client, label, file_blocks, all_reviews, prompt_template)

    full_report = header + '\n\n---\n\n'.join(all_reviews)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(full_report)

    print(f"\nReview written to: {out_path} ({len(full_report):,} chars)")
    lines = all_reviews[0].splitlines() if all_reviews else []
    print('\n'.join(lines[:40]))
    if len(lines) > 40:
        print(f"\n... (full review in {out_path})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Gemini code review for SlyLED')
    parser.add_argument('--key',      required=True, help='Gemini API key')
    parser.add_argument('--gyro',     action='store_true', help='Review gyro feature files')
    parser.add_argument('--spa',      action='store_true', help='Review SPA (index.html)')
    parser.add_argument('--camera',   action='store_true', help='Review camera modules')
    parser.add_argument('--server',   action='store_true', help='Review desktop server Python files')
    parser.add_argument('--firmware', action='store_true', help='Review ESP firmware C++ files')
    parser.add_argument('--design',   action='store_true', help='Review mover calibration v2 design (#488)')
    parser.add_argument('--all',      action='store_true', help='Review all groups (each as own batch)')
    parser.add_argument('--files',    nargs='+', metavar='FILE', help='Specific files (single group)')
    parser.add_argument('--no-fetch', action='store_true', help='Skip git fetch; use local working tree')
    parser.add_argument('--out',      default='review.md', help='Output markdown file (default: review.md)')
    args = parser.parse_args()

    # Determine which named groups to run
    use_git = not args.no_fetch
    if use_git:
        use_git = fetch_from_github()

    prompt_template = None  # default: REVIEW_PROMPT

    if args.all:
        selected_groups = ALL_GROUPS
    else:
        selected_groups = []
        if args.gyro:
            selected_groups.append(('gyro', GYRO_FILES))
        if args.spa:
            selected_groups.append(('spa', SPA_FILES))
        if args.camera:
            selected_groups.append(('camera', CAMERA_FILES))
        if args.server:
            selected_groups.append(('server', SERVER_FILES))
        if args.firmware:
            selected_groups.append(('firmware', FIRMWARE_FILES))
        if args.design:
            selected_groups.append(('calibration-design', CALIBRATION_DESIGN_FILES))
            prompt_template = DESIGN_REVIEW_PROMPT
            if not args.out or args.out == 'review.md':
                args.out = 'calibration-design-review.md'
        if args.files:
            selected_groups.append(('custom', args.files))

    if not selected_groups:
        parser.error("Specify at least one of: --gyro --spa --camera --firmware --all --files")

    # Load files for each group
    groups_with_blocks = []
    for label, paths in selected_groups:
        print(f"\nLoading group [{label}] ({len(paths)} file(s))...")
        blocks = load_files(paths, use_git=use_git)
        groups_with_blocks.append((label, blocks))

    if not any(blocks for _, blocks in groups_with_blocks):
        print("No files loaded — nothing to review.")
        sys.exit(1)

    run_review(args.key, groups_with_blocks, args.out, prompt_template)


if __name__ == '__main__':
    main()
