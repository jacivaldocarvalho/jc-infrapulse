"""Inspect only runtime ports/networks, never container environment or credentials."""

import argparse
import json
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("project")
parser.add_argument("--allow-local-interfaces", action="store_true")
args = parser.parse_args()
containers = subprocess.check_output(
    [
        "docker",
        "ps",
        "-q",
        "--filter",
        f"label=com.docker.compose.project={args.project}",
    ],
    text=True,
).split()
assert containers, "No running containers found"
for identifier in containers:
    output = subprocess.check_output(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .Name}} {{json .NetworkSettings.Ports}} {{json .NetworkSettings.Networks}}",
            identifier,
        ],
        text=True,
    )
    # Decode adjacent JSON values without inspecting .Config.Env.
    decoder = json.JSONDecoder()
    name, offset = decoder.raw_decode(output)
    ports, size = decoder.raw_decode(output[offset:].lstrip())
    remaining = output[offset:].lstrip()[size:].lstrip()
    networks, _ = decoder.raw_decode(remaining)
    published = []
    for container_port, bindings in (ports or {}).items():
        for binding in bindings or []:
            assert args.allow_local_interfaces, f"Unexpected host binding: {name}"
            assert binding["HostIp"] == "127.0.0.1", (name, binding)
            assert (container_port, binding["HostPort"]) in {
                ("8000/tcp", "8000"),
                ("3000/tcp", "3000"),
                ("8080/tcp", "8080"),
            }, (name, container_port, binding)
            published.append(binding)
    if name.endswith("-db-1"):
        assert set(networks) == {f"{args.project}_internal"}, networks
    if name.endswith("-frontend-1"):
        assert set(networks) == {f"{args.project}_api_access"}, networks
    print(name, "published=", published, "networks=", sorted(networks))
