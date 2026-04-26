import serial
import time

# --- CONFIGURATION ---
# Replace with your actual Mac serial port from Step 1
PORT = '/dev/cu.usbmodem5B141111901' 
BAUD_RATE = 1000000

# The servo's current ID (Default from factory is usually 1)
CURRENT_ID = 1
# The ID you want to assign (e.g., 2 for the second finger actuator)
NEW_ID = 8

def calculate_checksum(payload):
    """Calculates the Feetech protocol checksum."""
    return (~sum(payload)) & 0xFF

def send_write_command(ser, servo_id, address, data):
    """Constructs and sends a byte packet to write data to a specific register."""
    instruction = 0x03 # 0x03 is the WRITE command
    length = 4         # Length = Instruction(1) + Address(1) + Data(1) + Checksum(1)
    
    # Payload excludes the 0xFF 0xFF header
    payload = [servo_id, length, instruction, address, data]
    checksum = calculate_checksum(payload)
    
    # Complete packet: Header + Payload + Checksum
    packet = [0xFF, 0xFF] + payload + [checksum]
    
    ser.write(bytearray(packet))
    time.sleep(0.05) # Brief pause to allow the servo to process the write

def set_servo_id(port, current_id, new_id):
    try:
        with serial.Serial(port, BAUD_RATE, timeout=1) as ser:
            print(f"Connected to {port} at {BAUD_RATE} baud.")
            
            # 1. Unlock EEPROM (Register 0x30 set to 0)
            print("Unlocking EEPROM...")
            send_write_command(ser, current_id, 0x30, 0)
            
            # 2. Write new ID (Register 0x05)
            print(f"Writing new ID: {new_id}...")
            send_write_command(ser, current_id, 0x05, new_id)
            
            # 3. Lock EEPROM (Register 0x30 set to 1)
            print("Locking EEPROM...")
            send_write_command(ser, current_id, 0x30, 1)
            
            print(f"Success! The servo is now configured to ID {new_id}.")
            
    except serial.SerialException as e:
        print(f"Serial port error: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == '__main__':
    print("WARNING: Ensure ONLY ONE servo is connected to the bus to avoid ID collisions.")
    set_servo_id(PORT, CURRENT_ID, NEW_ID)