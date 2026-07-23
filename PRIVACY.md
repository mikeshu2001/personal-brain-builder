# Privacy

This builder is designed to run against folders the owner explicitly chooses.
It does not require a hosted database and does not configure publication,
cloud synchronization, telemetry, or a remote destination.

The generated personal memory is separate from this public builder. Local
operating files may contain source filenames and paths, so the generated
memory should be treated as private even when its knowledge cards look
harmless.

After setup, the used builder contains an ignored
`.personal-brain-pointer.json` with the absolute memory location and random
installation IDs. It contains no owner label or knowledge cards, but the path
is private metadata. Ordinary repository publication ignores it; manual
copying and newly created archives may still include it. Do not publish,
contribute, or re-archive a used builder copy without checking and removing
that pointer.

The AI application itself may process approved text through an online
service. Before content reading begins, the assistant must explain the
processing location it can determine and the owner must decide whether the
selected material may be processed there.

Protected credential locations and secret-shaped values are blocked. If a
secret appears unexpectedly, reading stops and the value must not be copied
into memory, logs, responses, or test material.

No personal files or generated memory belong in contributions to this
repository.
