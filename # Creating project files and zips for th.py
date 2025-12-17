# Creating project files and zips for the Complete IoT Fish Feeder package (backend, frontend, esp32, docs)
import os, json, textwrap, zipfile, pathlib

base = '/mnt/data/iot_fish_feeder'
os.makedirs(base, exist_ok=True)

# Backend app.py (clean, no SMS/push)
backend_dir = os.path.join(base, 'backend')
os.makedirs(backend_dir, exist_ok=True)
backend_app = textwrap.dedent("""\
    from flask import Flask, request, jsonify, render_template, session
    import firebase_admin
    from firebase_admin import credentials, firestore
    from datetime import datetime
    from flask_cors import CORS

    app = Flask(__name__)
    app.secret_key = "super_secret_key"
    CORS(app, supports_credentials=True)

    cred = credentials.Certificate("firebase-key.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    @app.route('/')
    def dashboard():
        readings_ref = (
            db.collection('devices')
            .document('ESP32_001')
            .collection('readings')
            .order_by('createdAt', direction=firestore.Query.DESCENDING)
            .limit(10)
        )
        readings = readings_ref.stream()
        data = []
        for r in readings:
            doc = r.to_dict()
            data.append({
                "temperature": doc.get("temperature"),
                "ph": doc.get("ph"),
                "ammonia": doc.get("ammonia"),
                "turbidity": doc.get("turbidity"),
                "createdAt": doc.get("createdAt").strftime("%Y-%m-%d %H:%M:%S") if doc.get("createdAt") else ""
            })
        data = list(reversed(data))
        summary = "🟢 All systems normal."
        alert_color = "green"
        if len(data) > 0:
            last = data[-1]
            if last["temperature"] > 30 or last["temperature"] < 20:
                summary = "Temperature out of range!"
                alert_color = "red"
            if last["ph"] < 6.5 or last["ph"] > 8.5:
                summary = "pH level abnormal!"
                alert_color = "orange"
            if last["ammonia"] > 0.5:
                summary = "High ammonia detected!"
                alert_color = "darkred"
            if last["turbidity"] > 50:
                summary = "Water too cloudy!"
                alert_color = "gold"
        return render_template("dashboard.html", readings=data, summary=summary, alert_color=alert_color)

    @app.route('/add_reading', methods=['POST'])
    def add_reading():
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400
        device_id = data.get("device_id", "ESP32_001")
        try:
            temperature = float(data.get("temperature"))
            ph = float(data.get("ph"))
            ammonia = float(data.get("ammonia"))
            turbidity = float(data.get("turbidity"))
        except:
            return jsonify({"status": "error", "message": "Invalid numeric values"}), 400
        db.collection('devices').document(device_id).collection('readings').add({
            "temperature": temperature,
            "ph": ph,
            "ammonia": ammonia,
            "turbidity": turbidity,
            "createdAt": datetime.utcnow()
        })
        return jsonify({"status": "success"}), 200

    @app.route('/control/mosfet', methods=['POST'])
    def mosfet_control():
        if 'user' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        data = request.json
        pump = data.get('pump', 0)
        feeder = data.get('feeder', 0)
        payload = {'pump': pump, 'feeder': feeder, 'updatedAt': datetime.utcnow()}
        db.collection('device_control').document('control_1').set(payload, merge=True)
        return jsonify({'msg': 'MOSFET control updated', 'payload': payload}), 200

    if __name__ == "__main__":
        app.run(host="0.0.0.0", port=5000, debug=True)
""")
with open(os.path.join(backend_dir, 'app.py'), 'w') as f:
    f.write(backend_app)

requirements = "Flask\nfirebase-admin\nflask-cors\nWerkzeug\nrequests\n"
with open(os.path.join(backend_dir, 'requirements.txt'), 'w') as f:
    f.write(requirements)

# Frontend minimal files (React Vite style)
frontend_dir = os.path.join(base, 'frontend')
os.makedirs(frontend_dir, exist_ok=True)
src_dir = os.path.join(frontend_dir, 'src')
os.makedirs(src_dir, exist_ok=True)

app_jsx = textwrap.dedent("""\
    import React, {useState} from 'react'
    import Login from './Login'
    import ControlPanel from './ControlPanel'

    export default function App(){
      const [view, setView] = useState('login')
      const [role, setRole] = useState(null)

      return (
        <div style={{padding:20}}>
          {view==='login' && <Login onLogin={(r)=>{ setRole(r); setView('control')} } />}
          {view==='control' && <ControlPanel role={role} />}
        </div>
      )
    }
""")
with open(os.path.join(src_dir, 'App.jsx'), 'w') as f:
    f.write(app_jsx)

login_jsx = textwrap.dedent("""\
    import React, {useState} from 'react'

    const API = 'http://YOUR_SERVER_IP:5000'

    export default function Login({onLogin}){
      const [email, setEmail] = useState('')
      const [password, setPassword] = useState('')
      const [status, setStatus] = useState('')

      async function handleLogin(){
        const r = await fetch(API+'/login', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({email,password}), credentials:'include'
        })
        const j = await r.json()
        if(r.ok){ setStatus('Login OK'); onLogin(j.role || 'user') }
        else setStatus(j.error || 'Login failed')
      }

      return (
        <div>
          <h3>Login</h3>
          <input placeholder='email' value={email} onChange={e=>setEmail(e.target.value)} /><br/>
          <input placeholder='password' type='password' value={password} onChange={e=>setPassword(e.target.value)} /><br/>
          <button onClick={handleLogin}>Login</button>
          <div>{status}</div>
        </div>
      )
    }
""")
with open(os.path.join(src_dir, 'Login.jsx'), 'w') as f:
    f.write(login_jsx)

control_jsx = textwrap.dedent("""\
    import React, {useState} from 'react'
    const API = 'http://YOUR_SERVER_IP:5000'

    export default function ControlPanel({role}){
      const [pump, setPump] = useState(0)
      const [feeder, setFeeder] = useState(0)
      const [status, setStatus] = useState('')

      async function sendControl(){
        const r = await fetch(API+'/control/mosfet', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({pump,feeder}), credentials:'include'
        })
        const j = await r.json()
        setStatus(JSON.stringify(j))
      }

      return (
        <div>
          <h3>Control Panel (role: {role})</h3>
          { (role==='admin' || role==='super_admin') ? (
            <div>
              <label>Pump <input type='checkbox' checked={pump===1} onChange={e=>setPump(e.target.checked?1:0)} /></label><br/>
              <label>Feeder <input type='checkbox' checked={feeder===1} onChange={e=>setFeeder(e.target.checked?1:0)} /></label><br/>
              <button onClick={sendControl}>Send</button>
            </div>
          ) : <div>Monitoring only</div> }
          <div>{status}</div>
        </div>
      )
    }
""")
with open(os.path.join(src_dir, 'ControlPanel.jsx'), 'w') as f:
    f.write(control_jsx)

package_json = {
  "name": "iot-fish-feeder-frontend",
  "version": "0.0.1",
  "private": True,
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "serve": "vite preview"
  },
  "dependencies": {
    "react": "^18.0.0",
    "react-dom": "^18.0.0"
  },
  "devDependencies": {
    "vite": "^4.0.0"
  }
}
with open(os.path.join(frontend_dir, 'package.json'), 'w') as f:
    f.write(json.dumps(package_json, indent=2))

# ESP32 firmware (polling version)
esp_dir = os.path.join(base, 'esp32')
os.makedirs(esp_dir, exist_ok=True)
esp_code = textwrap.dedent("""\
    #include <WiFi.h>
    #include <HTTPClient.h>
    #include <ArduinoJson.h>
    #include <ArduinoOTA.h>

    const char* ssid = "YOUR_SSID";
    const char* password = "YOUR_WIFI_PASS";
    const char* server = "http://YOUR_SERVER_IP:5000";

    const char* device_id = "ESP32_001";
    const char* device_token = "CHANGE_ME_DEVICE_TOKEN";

    const int pumpPin = 19;   // PWM
    const int feederPin = 18; // digital

    unsigned long lastPoll = 0;
    const unsigned long pollInterval = 2000;

    void setup(){
      Serial.begin(115200);
      pinMode(pumpPin, OUTPUT);
      pinMode(feederPin, OUTPUT);
      analogWriteResolution(8);

      WiFi.begin(ssid, password);
      while(WiFi.status()!=WL_CONNECTED){ delay(500); Serial.print('.'); }
      Serial.println(\"WiFi connected\");

      ArduinoOTA.begin();
    }

    void loop(){
      ArduinoOTA.handle();
      if(millis() - lastPoll < pollInterval) return;
      lastPoll = millis();

      if(WiFi.status()==WL_CONNECTED){
        HTTPClient http;
        String url = String(server) + \"/device/get_control?device_id=\" + device_id + \"&token=\" + device_token;
        http.begin(url);
        int httpCode = http.GET();
        if(httpCode == 200){
          String payload = http.getString();
          StaticJsonDocument<256> doc;
          DeserializationError err = deserializeJson(doc, payload);
          if(!err){
            int pump = doc[\"pump\"] | 0;
            int feeder = doc[\"feeder\"] | 0;
            int pwmVal = (pump>1) ? pump : (pump?255:0);
            analogWrite(pumpPin, pwmVal);
            digitalWrite(feederPin, feeder?HIGH:LOW);
          }
        }
        http.end();
      }
    }
""")
with open(os.path.join(esp_dir, 'firmware_polling.ino'), 'w') as f:
    f.write(esp_code)

# Docs
docs_dir = os.path.join(base, 'docs')
os.makedirs(docs_dir, exist_ok=True)
report_py = textwrap.dedent("""\
    import pandas as pd
    from firebase_admin import credentials, firestore, initialize_app
    from datetime import datetime
    import matplotlib.pyplot as plt

    cred = credentials.Certificate('../backend/firebase-key.json')
    initialize_app(cred)
    db = firestore.client()

    def generate_report(device_id='ESP32_001'):
        docs = db.collection('devices').document(device_id).collection('readings').order_by('createdAt').stream()
        rows = [d.to_dict() for d in docs]
        if not rows:
            print('No data')
            return
        df = pd.DataFrame(rows)
        df['createdAt'] = pd.to_datetime(df['createdAt'])
        df.to_excel('report.xlsx', index=False)
        plt.figure()
        plt.plot(df['createdAt'], df['temperature'])
        plt.title('Temperature over time')
        plt.xlabel('Time')
        plt.ylabel('°C')
        plt.tight_layout()
        plt.savefig('temperature_report.pdf')

    if __name__=='__main__':
        generate_report()
""")
with open(os.path.join(docs_dir, 'report_generator.py'), 'w') as f:
    f.write(report_py)

mosfet_md = textwrap.dedent("""\
    # MOSFET Wiring & Notes\n\n
    Parts:\n- Logic-level N-channel MOSFET (e.g. AO3407, IRLZ44)\n- Flyback diode (1N4007)\n- Gate resistor 220Ω\n- 10kΩ pull-down resistor\n\nWiring:\n- Source -> GND (common ground with ESP32)\n- Drain -> Motor negative terminal\n- Motor positive -> VIN (5V or battery +)\n- Gate -> ESP32 GPIO (via 220Ω)\n- Flyback diode across motor terminals (cathode to +VIN)\n\nNotes:\n- Use proper MOSFET rated for current and heatsink if needed.\n- Always common-ground the supplies.\n""")
with open(os.path.join(docs_dir, 'mosfet_wiring.md'), 'w') as f:
    f.write(mosfet_md)

readme = textwrap.dedent("""\
    IoT Fish Feeder - Package\n\n
    Folders:\n- backend: Flask app (replace firebase-key.json with your service account)\n- frontend: React frontend (vite)\n- esp32: ESP32 firmware (polling version)\n- docs: report generator and wiring notes\n\nInstructions:\n1. Edit placeholders (YOUR_SERVER_IP, YOUR_SSID, etc.)\n2. Install backend requirements and run `python app.py`\n3. Build frontend and deploy or use dev server\n4. Flash ESP32 firmware after editing WiFi/server tokens\n""")
with open(os.path.join(base, 'README.md'), 'w') as f:
    f.write(readme)

# Create zip files
zips = {}
for name in ['backend','frontend','esp32','docs']:
    zip_path = os.path.join('/mnt/data', f'{name}.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        dirpath = os.path.join(base, name)
        for root, _, files in os.walk(dirpath):
            for file in files:
                full = os.path.join(root, file)
                arc = os.path.relpath(full, base)
                zf.write(full, arc)
    zips[name] = zip_path

# Also create a combined zip
combined_zip = '/mnt/data/iot_fish_feeder_all.zip'
with zipfile.ZipFile(combined_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, _, files in os.walk(base):
        for file in files:
            full = os.path.join(root, file)
            arc = os.path.relpath(full, base)
            zf.write(full, arc)

print("Files created:")
for k,v in zips.items():
    print(f"{k}: {v}")
print("combined:", combined_zip)

# Output paths for user
combined_zip

