import serial
import time

PORT = "/dev/cu.usbmodem5B141111901"
BAUD = 1_000_000
TIMEOUT = 0.03  # short timeout for fast scan

def checksum(payload):
    return (~sum(payload)) & 0xFF

def ping_packet(servo_id: int):
    # Feetech / Dynamixel v1 style: FF FF ID LEN INST CHK
    payload = [servo_id, 0x02, 0x01]  # LEN=2, INST=PING(0x01)
    return bytes([0xFF, 0xFF] + payload + [checksum(payload)])

def scan_ids():
    found = []
    with serial.Serial(PORT, BAUD, timeout=TIMEOUT) as ser:
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        for sid in range(0, 254):
            ser.write(ping_packet(sid))
            time.sleep(0.002)

            # expected status packet is usually 6 bytes:
            # FF FF ID LEN ERR CHK
            resp = ser.read(6)
            if len(resp) == 6 and resp[0] == 0xFF and resp[1] == 0xFF and resp[2] == sid:
                found.append(sid)

    return found

if __name__ == "__main__":
    ids = scan_ids()
    if ids:
        print("Found servo IDs:", ids)
    else:
        print("No servos responded.")