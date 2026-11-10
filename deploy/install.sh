#!/usr/bin/env bash
# usage: sudo deploy/install.sh [--open] [public-hostname]
set -euo pipefail

auth=1
if [[ ${1:-} == --open ]]; then auth=0; shift; fi

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
user="${SUDO_USER:?run through sudo as the deploy user}"
home="$(getent passwd "$user" | cut -d: -f6)"
app="$(cd "$(dirname "$0")/.." && pwd)"
host="${1:-$(hostname -f)}"
etc=/etc/nginx/tape

DEBIAN_FRONTEND=noninteractive apt-get install -y -q nginx apache2-utils >/dev/null

for unit in tape-api tape-ui; do
    sed -e "s|@APP@|$app|g" -e "s|@USER@|$user|g" -e "s|@HOME@|$home|g" \
        "$app/deploy/systemd/$unit.service" > "/etc/systemd/system/$unit.service"
done

install -d -m 750 -o root -g www-data "$etc"
if [[ ! -f $etc/tls.crt ]]; then
    openssl req -x509 -newkey rsa:3072 -sha256 -days 825 -nodes -subj "/CN=$host" \
        -addext "subjectAltName=DNS:$host" -keyout "$etc/tls.key" -out "$etc/tls.crt" 2>/dev/null
fi
[[ -f $etc/htpasswd ]] || : > "$etc/htpasswd"
if (( auth )); then
    printf 'auth_basic "tape";\nauth_basic_user_file %s/htpasswd;\n' "$etc" > "$etc/auth.conf"
else
    : > "$etc/auth.conf"
fi
chown root:www-data "$etc/tls.key" "$etc/tls.crt" "$etc/htpasswd"
chmod 640 "$etc/tls.key" "$etc/htpasswd"

install -m 644 "$app/deploy/nginx/tape.conf" /etc/nginx/sites-available/tape
ln -sf /etc/nginx/sites-available/tape /etc/nginx/sites-enabled/tape
rm -f /etc/nginx/sites-enabled/default
nginx -t

systemctl daemon-reload
systemctl enable tape-api tape-ui >/dev/null
systemctl restart tape-api tape-ui
systemctl reload nginx
if (( auth )); then
    echo "installed: https://$host  login on, users: $(grep -c . "$etc/htpasswd" || true)"
else
    echo "installed: https://$host  login OFF"
fi
