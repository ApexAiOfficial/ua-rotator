"""Select from the bundled profile with per-domain stickiness."""

from apex_ua_rotator import UserAgentRotator

rotator = UserAgentRotator.from_bundled_profile(policy="per_key")

for hostname in ("example.com", "python.org", "example.com"):
    print(hostname, "=>", rotator.select(key=hostname))
