# LinuxCNC's standalone G-code interpreter (rs274), for scripts/linuxcnc_check.py.
FROM debian:trixie
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends linuxcnc-uspace >/dev/null && rm -rf /var/lib/apt/lists/*
