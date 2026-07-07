// Smart Locker Arduino Mega firmware for the Raspberry Pi gateway.
//
// Serial protocol expected by locker_gateway.py:
//   Pi GPIO UART <-> Mega Serial1:
//     Pi TXD GPIO14 pin 8  -> Mega RX1 pin 19
//     Pi RXD GPIO15 pin 10 <- Mega TX1 pin 18 through 5V-to-3.3V level shifting
//   Pi -> Mega: OPEN,<locker_id>
//   Pi -> Mega: LOCKER_USED,<locker_id>
//   Pi -> Mega: LOCKER_EMPTY,<locker_id>
//   Mega -> Pi: OK,<locker_id>
//   Mega -> Pi: DOOR_OPEN,<locker_id>
//   Mega -> Pi: DOOR_CLOSED,<locker_id>
//
// Adjust the pin arrays below to match the actual relay, LED, and door sensor wiring.

const long SERIAL_BAUD = 115200;
const byte LOCKER_COUNT = 8;

const byte RELAY_PINS[LOCKER_COUNT] = {22, 23, 24, 25, 26, 27, 28, 29};
const byte OCCUPIED_LED_PINS[LOCKER_COUNT] = {30, 31, 32, 33, 34, 35, 36, 37};
const byte DOOR_SENSOR_PINS[LOCKER_COUNT] = {38, 39, 40, 41, 42, 43, 44, 45};

const bool RELAY_ACTIVE_LOW = true;
const bool DOOR_CLOSED_LOW = true;
const unsigned long OPEN_PULSE_MS = 450;
const unsigned long DOOR_DEBOUNCE_MS = 60;

String inputLine;
bool lastDoorClosed[LOCKER_COUNT];
bool stableDoorClosed[LOCKER_COUNT];
unsigned long lastDoorChangeMs[LOCKER_COUNT];

#define GATEWAY_SERIAL Serial1

void setRelay(byte index, bool active) {
  digitalWrite(RELAY_PINS[index], relayLevel(active));
}

int relayLevel(bool active) {
  if (RELAY_ACTIVE_LOW) {
    return active ? LOW : HIGH;
  }
  return active ? HIGH : LOW;
}

bool readDoorClosed(byte index) {
  int value = digitalRead(DOOR_SENSOR_PINS[index]);
  return DOOR_CLOSED_LOW ? value == LOW : value == HIGH;
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  GATEWAY_SERIAL.begin(SERIAL_BAUD);
  inputLine.reserve(48);

  for (byte i = 0; i < LOCKER_COUNT; i++) {
    pinMode(RELAY_PINS[i], OUTPUT);
    setRelay(i, false);

    pinMode(OCCUPIED_LED_PINS[i], OUTPUT);
    digitalWrite(OCCUPIED_LED_PINS[i], LOW);

    pinMode(DOOR_SENSOR_PINS[i], INPUT_PULLUP);
    bool closed = readDoorClosed(i);
    lastDoorClosed[i] = closed;
    stableDoorClosed[i] = closed;
    lastDoorChangeMs[i] = 0;
  }

  Serial.println("MEGA SMART LOCKER 8 TU - SERIAL GATEWAY READY");
  GATEWAY_SERIAL.println("MEGA SMART LOCKER 8 TU - SERIAL GATEWAY READY");
}

void loop() {
  readSerialCommands();
  publishDoorEvents();
}

void readSerialCommands() {
  while (GATEWAY_SERIAL.available() > 0) {
    char ch = (char)GATEWAY_SERIAL.read();
    if (ch == '\r') {
      continue;
    }
    if (ch == '\n') {
      handleCommand(inputLine);
      inputLine = "";
      continue;
    }
    if (inputLine.length() < 47) {
      inputLine += ch;
    }
  }
}

void handleCommand(String line) {
  line.trim();
  if (line.length() == 0) {
    return;
  }

  int comma = line.indexOf(',');
  if (comma < 0) {
    GATEWAY_SERIAL.print("ERR,");
    GATEWAY_SERIAL.println(line);
    return;
  }

  String action = line.substring(0, comma);
  int lockerId = line.substring(comma + 1).toInt();
  if (lockerId < 1 || lockerId > LOCKER_COUNT) {
    GATEWAY_SERIAL.print("ERR,");
    GATEWAY_SERIAL.println(line);
    return;
  }

  byte index = (byte)(lockerId - 1);
  if (action == "OPEN") {
    openLocker(index);
    GATEWAY_SERIAL.print("OK,");
    GATEWAY_SERIAL.println(lockerId);
    return;
  }

  if (action == "LOCKER_USED") {
    digitalWrite(OCCUPIED_LED_PINS[index], HIGH);
    return;
  }

  if (action == "LOCKER_EMPTY") {
    digitalWrite(OCCUPIED_LED_PINS[index], LOW);
    return;
  }

  GATEWAY_SERIAL.print("ERR,");
  GATEWAY_SERIAL.println(line);
}

void openLocker(byte index) {
  setRelay(index, true);
  delay(OPEN_PULSE_MS);
  setRelay(index, false);
}

void publishDoorEvents() {
  unsigned long now = millis();
  for (byte i = 0; i < LOCKER_COUNT; i++) {
    bool closed = readDoorClosed(i);
    if (closed != lastDoorClosed[i]) {
      lastDoorClosed[i] = closed;
      lastDoorChangeMs[i] = now;
    }

    if (closed != stableDoorClosed[i] && now - lastDoorChangeMs[i] >= DOOR_DEBOUNCE_MS) {
      stableDoorClosed[i] = closed;
      GATEWAY_SERIAL.print(closed ? "DOOR_CLOSED," : "DOOR_OPEN,");
      GATEWAY_SERIAL.println(i + 1);
    }
  }
}
