#!/usr/bin/env bash
grep -n -B 2 -A 4 -E 'set_interface_ports|control_port|sock_in|sock_out|port_in|port_out' /tmp/SIM_JSON.cpp | head -80
