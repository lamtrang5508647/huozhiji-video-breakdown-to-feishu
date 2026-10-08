# Human Editing And Bot Continuity

Before calling a document complete, verify that the intended human recipient has direct editing or ownership rights, not merely a tenant-readable link. The bot being able to edit does not establish that the user can edit.

Resolve the exact target document token and receiving account. Names/screenshots alone are not unique IDs. A user's explicit confirmation that a recipient is the current Base owner can bind that name to the Base owner's freshly queried ID. Store that provenance in project configuration. Open IDs are application-scoped: never reuse them after switching apps or assume a corporate identity and a personal identity are interchangeable.

Reuse the working bot profile for authorized project updates. Do not initiate personal OAuth, change strict mode, or make the public link editable to compensate for a missing collaborator.

Read the current lark-drive and contact schemas before permission actions. For a newly created document within an authorized delivery, configure the verified project recipient's direct edit access and read it back. For an existing document, preserve the current owner and collaborators. Transfer ownership only for the concrete document and recipient the user requested; a previous transfer is not blanket authority over other documents.

For an authorized transfer, query current metadata first, use the supported transfer-owner command, keep the bot's required edit permission (`remove_old_owner=false`, `old_owner_perm=edit`) and keep location when supported (`stay_put=true`). Do not trigger optional notifications without need. Read metadata again and require the new owner ID to match the verified recipient. Also read current bot edit permission and the collaborator list if needed. Existing app grants may remain broader than the transfer parameter; report the actual readback, do not assert that every permission was downgraded.

Preserve public sharing settings. If the transfer or collaborator operation fails, retain the exact error and report the unresolved access; do not broaden sharing as a workaround. API owner/member readbacks verify permissions, not the human's logged-in UI. Keep that distinction in the receipt.
