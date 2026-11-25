---
title: Tenant transfer
---

<!-- Generated from idbroker: packages/auth/docs/tenant-transfer.md. Edit it there, not in Authifi/docs. -->

# Tenant transfer

v2 NDJSON is the supported way to export a tenant, edit the file, and import it into the current tenant. A v2 export contains no secret values. Secret-bearing fields hold a `$secret` reference.

JSON export (`GET /export`, `auth-cli tenants export --format json`) and JSON import (`POST /import`, `auth-cli tenants import` of a `.json` file) still work. They are deprecated. This notice does not name a removal release. A later change has to remove those paths. The JSON import behavior stays as it was on `main`: if preview or import finds invalid record data, it fails with a clear error that names the record and tells you to export again as `.ndjson` if you want to import past invalid records. An NDJSON import can continue past up to 50 invalid records (see [Continue past invalid records](#continue-past-invalid-records)).

`--displayName` and `--tenantId` apply only to a JSON import. An NDJSON file imports into the current tenant.

## Format

A transfer is NDJSON: one JSON object per line. The first line is the header, then one record per entity, then an end marker.

```json
{"kind":"header","formatVersion":2,"source":{"server":"https://auth.example.org","tenantId":"acme"},"exportedAt":"2026-09-27T00:00:00.000Z"}
{"kind":"group","key":"admins","data":{"name":"admins"}}
{"kind":"end"}
```

The header carries `formatVersion`, `source.server`, `source.tenantId`, and `exportedAt`. It has no counts, checksum, or key index, so deleting records does not make it stale.

Each record is `{kind, key, data}`. Records contain no database ids. `key` comes from the fields that identify the entity (the table's unique constraint). It is a string when the kind has one required key field (`"admins"`). Otherwise it is an object of the key fields that are present (`{"namespace":"ns","name":"admins"}`).

Records appear in this kind order:

1. `image`
2. `tenantSettings`
3. `namespace`
4. `identityProvider`
5. `group`
6. `resourceServer`
7. `umrsRole`
8. `client`
9. `requestableAccess`
10. `loginPage`
11. `errorPage`
12. `allowedOrigin`
13. `contact`

A reference names another record by that same key form. It may point only at an earlier kind, except for deferred references, which are resolved after the whole file is read. The tenant's `default` namespace is never written as a record and must not appear in a key (`TT_RESERVED_KEY`).

A missing end marker means the file was cut off (`TT_TRUNCATED`).

## Secrets

Secret-bearing fields hold a `$secret` reference, for example `{"$secret":{"scope":"tenant","name":"smtp"}}` or `{"$secret":{"inline":true}}`. The file must not contain the secret value, a raw credential id, or a JWK private member.

A system-scoped reference is resolved by name only when the header `source.server` is this server. Consumers key off the issue `code`, not the message:

- `TT_SECRET_PLACEHOLDER` — a created or updated record has secret fields this import does not carry (the file holds only `$secret` references). The import still writes the record; set those secrets after the import, before relying on the credential. Fields the destination already holds a secret for, or that the caller sent inline, are not listed.
- `TT_CLIENT_SECRET_GENERATED` — a new confidential client received a generated secret. Rotate it before use.

## Issue codes

Every issue has a stable `code`, a `severity` (`error` or `warning`), and, when known, `line`, `kind`, `key`, and a JSON `pointer` into the record's `data`. Do not match on `message`. Existing codes are not renamed.

| Code                         | Meaning                                                                                                    |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `TT_JSON`                    | A line is not valid JSON.                                                                                  |
| `TT_HEADER`                  | The header is missing, malformed, or has an unsupported version.                                           |
| `TT_ENVELOPE`                | A line is missing `kind`, `key`, or `data`, or has the wrong types.                                        |
| `TT_UNKNOWN_KIND`            | `kind` is not a transfer kind.                                                                             |
| `TT_KEY_MISMATCH`            | `key` does not match the key fields in `data`.                                                             |
| `TT_ORDER`                   | A record appears after a record of a later kind.                                                           |
| `TT_DUP_KEY`                 | The same key appears twice.                                                                                |
| `TT_RESERVED_KEY`            | A key uses the implicit `default` namespace, or a resource server identifier the destination reserves.     |
| `TT_AFTER_END`               | A line follows the end marker.                                                                             |
| `TT_TRUNCATED`               | The end marker is missing.                                                                                 |
| `TT_LINE_TOO_LONG`           | A line exceeds the reader's limit.                                                                         |
| `TT_TRANSFER_TOO_LARGE`      | The transfer exceeds the reader's limit.                                                                   |
| `TT_SCHEMA`                  | Record data fails that kind's schema.                                                                      |
| `TT_REF_SHAPE`               | A reference has the wrong shape for its target.                                                            |
| `TT_MISSING_REF`             | A required reference, or any reference in strict mode, resolves nowhere.                                   |
| `TT_LINK_DROPPED`            | An optional reference resolves nowhere and will be dropped on import.                                      |
| `TT_SECRET_REF`              | A `$secret` reference is malformed.                                                                        |
| `TT_NAME_CONFLICT`           | A name matches a destination entity except for case or trailing spaces. Imports match keys exactly.        |
| `TT_PRIVILEGED_LINK`         | A group links a privileged role, or a role holds a privileged permission, without being privileged itself. |
| `TT_CONFLICT`                | A row changed after the import, so undo skipped it.                                                        |
| `TT_SECRET_PLACEHOLDER`      | A record's secret fields are not carried by the import; set them after the import.                         |
| `TT_CLIENT_SECRET_GENERATED` | A new confidential client received a generated secret that must be rotated before use.                     |
| `TT_NON_REVERSIBLE`          | An effect of the import cannot be reverted by undo.                                                        |

Offline `check` validates the file against itself. Whether the destination database accepts the records is decided by the server dry run.

## Edit, check, and import

1. Export the current tenant: `auth-cli tenants export -o transfer.ndjson`.
2. Delete records by removing NDJSON lines, or pack to a directory, delete files, and unpack back to NDJSON. `pack` writes a bundle from NDJSON. `unpack` writes NDJSON from a bundle.
   - `auth-cli tenants bundle pack transfer.ndjson ./bundle`
   - delete the `<kind>/<key>.json` files you do not want (`header.json` stays)
   - `auth-cli tenants bundle unpack ./bundle -o transfer.ndjson`
3. Drop dangling links: `auth-cli tenants bundle prune transfer.ndjson -o pruned.ndjson`. `--cascade` also drops records whose required parent is gone. `-o` leaves the source unchanged. Without `-o`, prune rewrites the file in place and asks for confirmation.
4. Check offline: `auth-cli tenants bundle check pruned.ndjson --json`. `--strict` reports unresolved references as `TT_MISSING_REF` errors instead of `TT_LINK_DROPPED` warnings.
5. Dry-run against the server: `auth-cli tenants import pruned.ndjson --dry-run --json`. Read `planHash` and the issues. Do not pass `--yes`. `--dry-run` does not change the command's write classification.
6. After approval, apply with the hash from the dry run: `auth-cli tenants import pruned.ndjson --expect-plan <hash>`. A mismatch writes nothing.
7. If the result includes `TT_SECRET_PLACEHOLDER` or `TT_CLIENT_SECRET_GENERATED`, replace or rotate that secret before use.
8. To reverse the import, dry-run first: `auth-cli tenants imports undo <runId> --dry-run`. The real undo, `auth-cli tenants imports undo <runId>`, is destructive. Run it only after approval. `TT_CONFLICT` means that row was left as it is. `TT_NON_REVERSIBLE` cannot be undone.

## Continue past invalid records

The dry run reports every invalid record as an error. Unresolved optional references stay `TT_LINK_DROPPED` warnings unless you pass `strict`, which makes them errors. When every error is about one record's data (the result has `skippableOnly: true`), it still returns a `planHash`, and you can apply with `continueOnError=true` and `expectPlan=<hash>`. More than 50 invalid records (`TT_TOO_MANY_ERRORS`) cannot be continued past. The limit counts records, so one record with many errors counts once.

Authorization, upload permission, privileged changes, caller policy, the plan hash, and the destination checks still run before anything is written. A refusal there writes nothing. The privileged-link checks also run as if the flagged records were left out, since a left-out group keeps its links and a left-out resource server keeps its role flags.

While applying, the import:

- leaves out the records the dry run flagged;
- leaves out a record that requires a failed one the destination does not already have, with the reason `depends on <kind> <key>, which failed`;
- leaves out a record the database refuses for its data (for example, a name too long for its column), and keeps going;
- drops an optional link to a failed record the destination does not already have, with a `TT_LINK_DROPPED` warning. A link to an existing record whose update failed stays, so the destination keeps that access. When an update's single-valued link (such as a client's namespace) names a failed record, the record keeps the link it has in the destination;
- stops and undoes the run when a resource server update that changes role privilege fails, since groups were checked against the new flags.

The result lists `written` (counts by plan action), `failedRecords` (kind, key, line, reason, and `dependent` for records left out because they required a failed one; at most 50), and the warnings. If a 51st record fails, or anything else stops the import (for example, the image scanner is unavailable), the import tries to undo everything it wrote and returns `undoReason` (`tooManyFailures` or `error`) and `message`. `undone` is true only when every change was reverted. When it is false, undo skipped some rows (for example a row someone changed since), so check the run in the import history. If the undo itself fails, the response is an error that names the run to undo. Errors found before the run starts, such as an invalid header, a denied upload, or a scanner failure while planning, return the preview result or an error, with no `written`, `failedRecords`, or `undone`. While a run is running or being undone, other imports into the tenant are refused, and so is an import that read the tenant before another run finished undoing. A stopped run returns HTTP 200 with `ok: false` and `undoReason` set, not an HTTP error, so scripts must check `ok`.

Because failed lines are left out, a run's `lastAppliedLine` is progress only. Undo reads the run's journal, not that line.

`auth-cli tenants bundle convert <v1.json>` prints v2 NDJSON from an old JSON export, and writes a file when `-o <file>` is passed. `auth-cli tenants bundle schema` prints the JSON Schema for one kind (`--kind client`) or every kind. `auth-cli tenants imports list` lists import runs.

The admin UI accepts `.ndjson` and `.json` on the tenant import dialog. `.ndjson` is the supported format. `.json` is the deprecated v1 import. When the preview finds only invalid records, the dialog asks whether to continue past them (up to 50) or cancel. After the import, it shows what was written, the records left out, the warnings, or a plain message that the import was undone.
