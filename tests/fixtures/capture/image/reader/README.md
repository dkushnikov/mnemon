# Recorded event streams of the reader's call

Two event streams of the real reader call (capture layer §21.4, §21.13 «Recorded output»), scrubbed by hand
for this public repository. The adapter's parser (`bin/capture/image_reader.py`) is tested against them
(`tests/capture_layer/test_image_reader.py`), and the stub reader (`../stub-claude-image.py`) builds every
mode's stream from them.

| File | The call | Exit | stderr |
|---|---|---|---|
| `success.jsonl` | a success on a synthetic quote card: `init`, `assistant` (one `StructuredOutput` tool use), `user` (its tool result), `rate_limit_event`, `result` (`success`) | 0 | empty |
| `budget.jsonl` | the same call over a budget of $0.0001: `init`, `assistant`, `user`, `result` (`error_max_budget_usd`, `is_error: true`, no `structured_output`, no `result` string, one `errors` string) | 1 | empty |

## How they were made

Claude Code 2.1.328 on the maintainer's macOS, 2026-11-27; the model the call reported is `claude-haiku-5-5`.
The argv was the reader's call of §21.4 (the fence flags, `--input-format stream-json --output-format
stream-json --verbose`, the schema `image-reading/1`, `--max-turns 4`, `--model`, `--effort`,
`--max-budget-usd`), with
`CLAUDE_CODE_EFFORT_LEVEL` set to the level, the vault-lock variables removed, and the message of §21.4 on
stdin: one `user` message with the image of a synthetic quote card in base64 and the adapter's prompt. The
budget stream is the same call with `--max-budget-usd 0.0001`. Stdout was kept as it came, one event per line.

## What was removed

Each event keeps its type, its place in the stream and its keys; the values below were replaced.

- Identifiers and times, by `<scrubbed>`: `session_id`, `uuid`, `request_id`, `timestamp`,
  `credential_expires_at`, the message id and the tool-use id of the `assistant` event (also as the key of
  `wire_tool_inputs`), the tool-use ids of the `user` event.
- Paths and account fields of `init`, by `<scrubbed>`: `cwd`, `original_cwd`, `apiKeySource`. The lists
  `plugins`, `skills`, `slash_commands`, `terminal_slash_commands` and `agents` are empty.
- The account's rate-limit state of `rate_limit_event`: every field set to a neutral value (`allowed`, `0`,
  `0.0`, `false`); the keys and their types are kept.
- The model's text, by the fixed sentence `A fixed synthetic sentence stands here in place of the text the
  model wrote.`: the tool use's `input.text` and `wire_tool_inputs` of the `assistant` event, and in
  `success.jsonl` the result's `structured_output.text` and its `result` string (the compact JSON form of
  `structured_output`, as the client writes it).

Kept as recorded: every field the adapter reads, with its real type — `init.tools` (`["StructuredOutput"]`),
`init.mcp_servers` (`[]`), `init.model` (`claude-haiku-5-5`), `init.claude_code_version` (`2.1.328`), and of
the `result` event `subtype`, `is_error`, `structured_output`, `total_cost_usd`, `duration_ms`, `num_turns`,
`permission_denials`, `modelUsage` (with `outputTokens` and `thinkingTokens`) and `errors` — and the token
counts, durations and client flags. Each file was checked for a home path, a host name and a session id
before it was kept; the test suite checks again that no identifier-shaped string and no absolute path is left.
