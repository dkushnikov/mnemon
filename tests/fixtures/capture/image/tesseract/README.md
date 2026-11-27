# Recorded output of the real tesseract

What the real witness wrote, recorded once by hand on 2026-11-27 with tesseract 5.5.4 (leptonica 1.87.0, the Homebrew build on macOS, arm64). The tests read these files; no test runs the real tool (capture layer §21.13 «Recorded output»). Edit nothing here by hand: record again with the real tool when its version changes.

`cases.json` lists every case: the arguments after the command, the exit status, and the files that hold its output.

## How each file was made

Every command ran with an environment of `PATH=/usr/bin:/bin` and `LANG=C`, from a scratch directory, with the absolute path of the Homebrew `tesseract`. Each file path given to tesseract was relative to that directory, so no recorded line names a home path or a host name. After each recording, a check found neither in the file.

| File | Command | Exit |
|---|---|---|
| `version.txt` | `tesseract --version`, its stdout (stderr was empty). The second line is ` leptonica-1.87.0`, with its leading space. | 0 |
| `list-langs.txt` | `tesseract --list-langs`, its stdout (stderr was empty). The header names the installed language model directory, `/opt/homebrew/share/tessdata/`. | 0 |
| `out.txt`, `out.tsv`, `run.stderr` | `tesseract quote-short.jpg scratch/out --tessdata-dir /opt/homebrew/share/tessdata -l eng --psm 3 -c tessedit_create_txt=1 -c tessedit_create_tsv=1`: the argv of §21.3. `quote-short.jpg` is a synthetic quote card of 1200 × 660 px. `run.stderr` holds the one diagnostic line of a clean run. | 0 |
| `too-large.stderr` | The same argv on `wide.png`, a 33 000 × 8 one-bit white PNG written with `zlib` and `struct`. tesseract wrote an empty `.txt` and a header-only `.tsv` before it failed. | 1 |
| `decode.stderr` | The same argv on `idat-broken.png`, a 16 × 16 one-bit PNG. Its `IDAT` chunk has a valid CRC, but its data is no zlib stream: `78 9c`, then `00 ff` twenty times. tesseract wrote an empty `.txt` and an empty `.tsv` before it failed. | 1 |
| `partial-lang.stderr` | The argv of §21.3 under `-l rus+eng` with `--tessdata-dir tessdata`. The directory `tessdata` held a copy of the installed `eng.traineddata` and a corrupt `rus.traineddata`: the line `this is not a traineddata file`, 64 times. tesseract wrote `.txt` and `.tsv`, and its `.txt` was byte-identical to the `eng` run above. | 0 |
| `no-lang.stderr` | The same as `partial-lang`, under `-l rus` alone. tesseract wrote no output file. | 1 |

The corrupt `rus.traineddata` gave the same message as a missing file, `Error opening data file tessdata/rus.traineddata`. Then tesseract printed `Failed loading language 'rus'`.
