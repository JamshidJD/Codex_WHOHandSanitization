# SmartSanitizer API

ASP.NET Core Web API for recording sanitizer usage in Microsoft SQL Server.

This project is part of the WHO Hand Sanitization workspace and is located at:

```text
C:\VSO\WHOHandSantization\SmartSanitizerWeb
```

## Database

The app connects to:

```text
Server=localhost
Database=db_SmartSanitization
User Id=sa
Password=digitalpass1A
```

On startup, the app creates `db_SmartSanitization` if needed, then creates these tables if they do not already exist:

- `dbo.Santizers`
  - `SanitizerID`
  - `MAC`
  - `Location`
- `dbo.doctors`
  - `DoctorID`
  - `DoctorName`
  - `DoctorRFIDTag`
- `dbo.SanitizationLog`
  - `DoctorID`
  - `SantizerID`
  - `StartTime`
  - `Duration`

`MAC` and `DoctorRFIDTag` are unique so repeated API calls reuse existing records.

When `SmartSanitizer.exe` is started on a machine where the database does not exist yet, it first connects to SQL Server `master`, creates `db_SmartSanitization`, then connects to the new database and creates the required tables. The configured SQL login must have permission to create databases and tables. If old detached/orphaned `.mdf` or `.ldf` files already exist in SQL Server's data folder, the app creates the database with unique physical file names so startup does not fail on file-name conflicts.

The startup check is also responsible for applying schema changes added later. When a future prompt requires a new table, column, index, setting, or constraint, add it to the startup schema sync in `DatabaseInitializer.EnsureSchemaIsCurrentAsync`; it will run every time the `.exe` launches and update existing databases without needing a manual SQL script. Applied schema state is tracked in `dbo.AppSchemaVersion`.

## Run

```powershell
cd C:\VSO\WHOHandSantization\SmartSanitizerWeb
dotnet run --launch-profile http
```

Dashboard:

```text
http://localhost:5000/
```

Swagger UI:

```text
http://localhost:5000/swagger
```

## API

Management and reporting endpoints:

```text
GET  /api/doctors
POST /api/doctors
GET  /api/sanitizers
POST /api/sanitizers
GET  /api/reports/sanitization_logs
GET  /api/kpi/minimum_duration
PUT  /api/kpi/minimum_duration
GET  /api/kpi/doctors
```

### Create sanitization log

```http
POST /api/sanitization_log
Content-Type: application/json
```

Request body:

```json
{
  "doctorRFIDTag": "RFID-1001",
  "sanitizerMAC": "AA:BB:CC:DD:EE:FF",
  "duration": 30
}
```

Behavior:

- Looks up `dbo.doctors` by `DoctorRFIDTag`.
- Looks up `dbo.Santizers` by `MAC`.
- If the doctor is missing, inserts it with `DoctorName = "Unknown"`.
- If the sanitizer is missing, inserts it with `Location = "Unknown"`.
- Inserts a row in `dbo.SanitizationLog` with the matched or newly created IDs.

Successful response:

```json
{
  "doctorID": 1,
  "santizerID": 1,
  "startTime": "2026-05-05T14:49:00Z",
  "duration": 30,
  "doctorCreated": true,
  "sanitizerCreated": true
}
```
