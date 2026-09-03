# Contributing

Format discoveries are the most valuable contribution. When you find out what a byte does:

1. Verify it two ways (e.g. Project rejects/accepts the file *and* a Project-resaved file shows it).
2. Add it to `docs/FORMAT_NOTES.md`.
3. Open an issue with the **Format finding** template linking the note and the test.

Never commit `.mpp` files written by Microsoft Project or taken from another project's test suite.
Never derive code from decompiled proprietary binaries.
