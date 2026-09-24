# beacon-on-moveiiia

The [MOVE-III beacon firmware](../beacon) running on the MOVE-IIIa payload
carrier board: RS422 (UART1) is the payload link, the SC850SL is the RGB
camera over 2-lane CSI, the MI48Dx drives the thermal camera over SPI, and a
PCM5102A DAC over I2S is the SSTV downlink.

All mission logic (idle loop, Robot36 SSTV transmission, CSP/KISS link
protocol, OTA firmware update) is the `beacon` crate, imported unchanged. This
repo only adds the MOVE-IIIa bring-up:

| beacon role    | MOVE-IIIa carrier          |
| -------------- | -------------------------- |
| payload link   | RS422 (UART1)              |
| RGB camera     | SC850SL, 2-lane CSI, RAW10 |
| thermal camera | MI1602 via MI48Dx, SPI     |
| audio out      | PCM5102A DAC over I2S      |

## Build and flash

Uses `../beacon`'s ESP-IDF install via the `.embuild` symlink (create it with
`ln -s ../beacon/.embuild .embuild` on a fresh checkout).

```sh
cargo build            # or --release
cargo run              # flash + monitor
NO_MONITOR=1 cargo run # flash only (leave the port free for the link)
```

To exercise the boot init-failure downlink without unplugging a camera, build
with `--features no-rgb-camera` and/or `--features no-thermal-camera`.
