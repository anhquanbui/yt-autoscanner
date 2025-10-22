### 3️⃣ MongoDB Setup (for beginners)

This project requires a running MongoDB database.  
You can either **install MongoDB locally** or **run it using Docker** — both methods are supported.

---

#### 🧩 Option A — Install MongoDB Community Server (recommended for non-developers)

1. **Download & install**
   - Visit 👉 [https://www.mongodb.com/try/download/community](https://www.mongodb.com/try/download/community)
   - Choose your OS (Windows / macOS / Linux)
   - Follow the installation wizard — make sure the **MongoDB Server** service is enabled to start automatically.

2. **Verify installation**
   ```bash
   mongo --version
   ```
   If you see a version number, MongoDB is installed successfully.

3. **(Optional) Install MongoDB Compass**
   - Download 👉 [https://www.mongodb.com/try/download/compass](https://www.mongodb.com/try/download/compass)
   - Open Compass and connect using:
     ```
     mongodb://localhost:27017
     ```
   - You’ll see databases like `admin`, `config`, and `local` by default.  
     The project’s default database is **`ytscan`** (it will be created automatically when scripts run).

4. **Run the helper script (to create indexes)**
   ```powershell
   python tools/make_indexes_v2.py
   ```

> 💡 **Tips for beginners:**  
> - You only need **MongoDB Server** (Compass is optional, but helpful to visualize data).  
> - The script `make_indexes_v2.py` can be run multiple times — existing indexes are automatically skipped.  
> - No need to install `pymongo` manually — it’s already included in `dev-requirements.txt`.

---

#### 🐳 Option B — Run MongoDB via Docker (for developers)

```bash
# Start MongoDB in Docker
docker run -d --name mongo -p 27017:27017 mongo:7

# Create indexes using the helper script
python tools/make_indexes_v2.py
```

> ✅ **Recommended if you prefer a clean, isolated setup**  
> Your MongoDB data will be stored in the container, which can be removed or re-created anytime.
