"""Tiny ONVIF client: asks the camera for its RTSP stream URLs.

Saves you from guessing the RTSP path of your IPC camera. Uses
WS-UsernameToken digest auth and falls back to no auth.
"""
import base64
import datetime
import hashlib
import os
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlsplit, urlunsplit
from xml.sax.saxutils import escape

ENV = '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">{hdr}<s:Body>{body}</s:Body></s:Envelope>'


def _security(user, pwd):
    if not user:
        return ""
    nonce = os.urandom(16)
    created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + pwd.encode()).digest()).decode()
    return (
        '<s:Header><Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        f"<UsernameToken><Username>{escape(user)}</Username>"
        '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</Password>"
        '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{base64.b64encode(nonce).decode()}</Nonce>"
        '<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        f"{created}</Created></UsernameToken></Security></s:Header>"
    )


def _call(url, body, user, pwd):
    last = None
    for auth in ((user, pwd), ("", "")) if user else (("", ""),):
        data = ENV.format(hdr=_security(*auth), body=body).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/soap+xml; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                return ET.fromstring(r.read())
        except Exception as e:
            last = e
    raise RuntimeError(f"ONVIF call to {url} failed: {last}")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def find_streams(host, port, user, pwd):
    dev = f"http://{host}:{int(port)}/onvif/device_service"
    media = dev
    try:
        caps = _call(dev, '<GetCapabilities xmlns="http://www.onvif.org/ver10/device/wsdl"><Category>Media</Category></GetCapabilities>', user, pwd)
        for el in caps.iter():
            if _local(el.tag) == "Media":
                for c in el:
                    if _local(c.tag) == "XAddr" and c.text:
                        media = c.text.strip()
    except RuntimeError:
        pass  # many cams serve media on the device URL anyway

    prof = _call(media, '<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>', user, pwd)
    out = []
    for p in prof.iter():
        if _local(p.tag) != "Profiles":
            continue
        token = p.attrib.get("token")
        name, w, h = token, None, None
        for el in p.iter():
            t = _local(el.tag)
            if t == "Name" and el.text and name == token:
                name = el.text
            elif t == "Width" and el.text and w is None:
                w = int(el.text)
            elif t == "Height" and el.text and h is None:
                h = int(el.text)
        body = ('<GetStreamUri xmlns="http://www.onvif.org/ver10/media/wsdl"><StreamSetup>'
                '<Stream xmlns="http://www.onvif.org/ver10/schema">RTP-Unicast</Stream>'
                '<Transport xmlns="http://www.onvif.org/ver10/schema"><Protocol>RTSP</Protocol></Transport>'
                f"</StreamSetup><ProfileToken>{escape(token or '')}</ProfileToken></GetStreamUri>")
        try:
            r = _call(media, body, user, pwd)
            uri = next((e.text.strip() for e in r.iter() if _local(e.tag) == "Uri" and e.text), None)
        except RuntimeError:
            uri = None
        if uri:
            out.append({"token": token, "name": name, "width": w, "height": h, "uri": _with_creds(uri, user, pwd)})
    out.sort(key=lambda s: (s["width"] or 99999) * (s["height"] or 99999))
    return out


def _with_creds(uri, user, pwd):
    if not user:
        return uri
    parts = urlsplit(uri)
    host = parts.hostname or ""
    netloc = f"{quote(user, safe='')}:{quote(pwd, safe='')}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
