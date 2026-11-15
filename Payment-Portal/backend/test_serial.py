import serial, time, json, sys
s = serial.Serial(sys.argv[1], 115200, timeout=1)
time.sleep(2); s.reset_input_buffer()
req = json.dumps({"type":"session_request","id":"test-1"}, separators=(",",":"))
print(">>", req); s.write((req + "\n").encode())
end = time.time() + 25
while time.time() < end:
    line = s.readline().decode(errors="replace").strip()
    if line: print("<<", line)
