# Contributing

Format discoveries are the most valuable contribution. When you find out what a byte does:

1. Verify it two ways (e.g. Project rejects/accepts the file *and* a Project-resaved file shows it).
2. Add it to `docs/FORMAT_NOTES.md`.
3. Open an issue with the **Format finding** template linking the note and the test.

Never commit `.mpp` files written by Microsoft Project or taken from another project's test suite.
Never derive code from decompiled proprietary binaries.

## How to find out what a byte does

These methods, in increasing order of power, produced every finding in
`docs/FORMAT_NOTES.md`. All the tools live in `scripts/`.

1. **Byte-diff Project-written references.** Real `.mpp` files you own are a
   corpus of valid encodings. `scripts/analyze_mpp.py` dumps task records
   field-by-field; `scripts/diff_mpp_tasks.py` and `scripts/diff_mpp.py`
   compare two files through their own field maps.
2. **Probe MPXJ's parser with candidate bytes.** MPXJ's observable read
   behaviour is a fast oracle: write a guess into a copy of the template, read
   it back with `scripts/mpxj_oracle.py`, iterate. This is how the calendar
   day-block layout was decoded without any reference sample. *Caveat:* MPXJ
   reads leniently — MPXJ agreement does **not** prove Project will accept the
   bytes (Project silently ignored two whole calendar dialects MPXJ read
   happily). Always finish with a check in Project itself.
3. **The resave experiment.** Open a generated file in Microsoft Project,
   change *one thing* by hand, Save As, and diff the two files with
   `scripts/diff_mpp.py`. Whatever Project rewrote is the encoding of that one
   thing. This found the planned-work contour, the calendar meta flags and the
   Var2Data length gates.
4. **Stream-transplant bisection.** When a generated file misbehaves and a
   Project-saved one works, transplant streams between them (`load_cfb` /
   `write_cfb` make this a ten-line script) and test each hybrid in Project.
   Each round halves the search space; three hybrids once isolated a
   one-dword Props gate from a ~100 KB haystack.

Check your changes against the golden round-trip: generate a file, resave it
in Project, and run `scripts/roundtrip_check.py generated.mpp resaved.mpp`.
