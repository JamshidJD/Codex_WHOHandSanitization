using Microsoft.Data.SqlClient;
using System.Data;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();
builder.Services.AddSingleton<DatabaseInitializer>();
builder.Services.AddScoped<SanitizationLogService>();
builder.Services.AddScoped<ManagementService>();

var app = builder.Build();

await app.Services.GetRequiredService<DatabaseInitializer>().EnsureCreatedAsync();

app.UseDefaultFiles();
app.UseStaticFiles();
app.UseSwagger();
app.UseSwaggerUI();

app.MapPost("/api/sanitization_log", async (
    SanitizationLogRequest request,
    SanitizationLogService service,
    CancellationToken cancellationToken) =>
{
    if (string.IsNullOrWhiteSpace(request.DoctorRFIDTag))
    {
        return Results.BadRequest(new { error = "DoctorRFIDTag is required." });
    }

    if (string.IsNullOrWhiteSpace(request.SanitizerMAC))
    {
        return Results.BadRequest(new { error = "SanitizerMAC is required." });
    }

    if (request.Duration <= 0)
    {
        return Results.BadRequest(new { error = "Duration must be greater than zero." });
    }

    var result = await service.RegisterLogAsync(request, cancellationToken);
    return Results.Created("/api/sanitization_log", result);
})
.WithName("CreateSanitizationLog");

app.MapGet("/api/doctors", async (ManagementService service, CancellationToken cancellationToken) =>
{
    var doctors = await service.GetDoctorsAsync(cancellationToken);
    return Results.Ok(doctors);
});

app.MapPost("/api/doctors", async (
    DoctorRequest request,
    ManagementService service,
    CancellationToken cancellationToken) =>
{
    if (string.IsNullOrWhiteSpace(request.DoctorName))
    {
        return Results.BadRequest(new { error = "DoctorName is required." });
    }

    if (string.IsNullOrWhiteSpace(request.DoctorRFIDTag))
    {
        return Results.BadRequest(new { error = "DoctorRFIDTag is required." });
    }

    var doctor = await service.AddDoctorAsync(request, cancellationToken);
    return Results.Created($"/api/doctors/{doctor.DoctorID}", doctor);
});

app.MapGet("/api/sanitizers", async (ManagementService service, CancellationToken cancellationToken) =>
{
    var sanitizers = await service.GetSanitizersAsync(cancellationToken);
    return Results.Ok(sanitizers);
});

app.MapPost("/api/sanitizers", async (
    SanitizerRequest request,
    ManagementService service,
    CancellationToken cancellationToken) =>
{
    if (string.IsNullOrWhiteSpace(request.MAC))
    {
        return Results.BadRequest(new { error = "MAC is required." });
    }

    var sanitizer = await service.AddSanitizerAsync(request, cancellationToken);
    return Results.Created($"/api/sanitizers/{sanitizer.SanitizerID}", sanitizer);
});

app.MapGet("/api/reports/sanitization_logs", async (
    ManagementService service,
    CancellationToken cancellationToken) =>
{
    var logs = await service.GetSanitizationLogsAsync(cancellationToken);
    return Results.Ok(logs);
});

app.MapGet("/api/kpi/minimum_duration", async (ManagementService service, CancellationToken cancellationToken) =>
{
    var minimumDuration = await service.GetMinimumDurationAsync(cancellationToken);
    return Results.Ok(new { minimumDuration });
});

app.MapPut("/api/kpi/minimum_duration", async (
    MinimumDurationRequest request,
    ManagementService service,
    CancellationToken cancellationToken) =>
{
    if (request.MinimumDuration <= 0)
    {
        return Results.BadRequest(new { error = "MinimumDuration must be greater than zero." });
    }

    var minimumDuration = await service.SetMinimumDurationAsync(request.MinimumDuration, cancellationToken);
    return Results.Ok(new { minimumDuration });
});

app.MapGet("/api/kpi/doctors", async (ManagementService service, CancellationToken cancellationToken) =>
{
    var performance = await service.GetDoctorPerformanceAsync(cancellationToken);
    return Results.Ok(performance);
});

app.Run();

public sealed record SanitizationLogRequest(
    string DoctorRFIDTag,
    string SanitizerMAC,
    int Duration);

public sealed record SanitizationLogResponse(
    int DoctorID,
    int SantizerID,
    DateTime StartTime,
    int Duration,
    bool DoctorCreated,
    bool SanitizerCreated);

public sealed record DoctorRequest(
    string DoctorName,
    string DoctorRFIDTag);

public sealed record DoctorDto(
    int DoctorID,
    string? DoctorName,
    string DoctorRFIDTag);

public sealed record SanitizerRequest(
    string MAC,
    string? Location);

public sealed record SanitizerDto(
    int SanitizerID,
    string MAC,
    string? Location);

public sealed record SanitizationLogDto(
    int DoctorID,
    string? DoctorName,
    string DoctorRFIDTag,
    int SantizerID,
    string SanitizerMAC,
    string? Location,
    DateTime StartTime,
    int Duration);

public sealed record MinimumDurationRequest(int MinimumDuration);

public sealed record DoctorPerformanceDto(
    int DoctorID,
    string? DoctorName,
    string DoctorRFIDTag,
    int TotalLogs,
    int SuccessfulLogs,
    int MissedLogs,
    decimal AverageDuration,
    decimal CompliancePercent,
    int MinimumDuration);

public sealed class DatabaseInitializer(IConfiguration configuration)
{
    private const string DatabaseName = "db_SmartSanitization";
    private const int CurrentSchemaVersion = 1;

    public async Task EnsureCreatedAsync()
    {
        var databaseConnectionString = configuration.GetConnectionString("SmartSanitizationDb")
            ?? throw new InvalidOperationException("Connection string 'SmartSanitizationDb' is missing.");

        await EnsureDatabaseExistsAsync(databaseConnectionString);
        await EnsureSchemaIsCurrentAsync(databaseConnectionString);
    }

    private static async Task EnsureDatabaseExistsAsync(string databaseConnectionString)
    {
        var builder = new SqlConnectionStringBuilder(databaseConnectionString)
        {
            InitialCatalog = "master"
        };

        await using var connection = new SqlConnection(builder.ConnectionString);
        await connection.OpenAsync();

        var escapedDatabaseLiteral = DatabaseName.Replace("'", "''");
        var sql = $"""
            IF DB_ID(N'{escapedDatabaseLiteral}') IS NULL
            BEGIN
                DECLARE @DataPath NVARCHAR(4000) = CAST(SERVERPROPERTY(N'InstanceDefaultDataPath') AS NVARCHAR(4000));
                DECLARE @LogPath NVARCHAR(4000) = CAST(SERVERPROPERTY(N'InstanceDefaultLogPath') AS NVARCHAR(4000));
                DECLARE @Suffix NVARCHAR(32) = REPLACE(CONVERT(NVARCHAR(36), NEWID()), N'-', N'');
                DECLARE @Sql NVARCHAR(MAX);

                IF @DataPath IS NULL
                BEGIN
                    SELECT TOP (1) @DataPath = LEFT(physical_name, LEN(physical_name) - CHARINDEX(N'\', REVERSE(physical_name)) + 1)
                    FROM sys.master_files
                    WHERE database_id = 1 AND file_id = 1;
                END;

                IF @LogPath IS NULL
                BEGIN
                    SELECT TOP (1) @LogPath = LEFT(physical_name, LEN(physical_name) - CHARINDEX(N'\', REVERSE(physical_name)) + 1)
                    FROM sys.master_files
                    WHERE database_id = 1 AND file_id = 2;
                END;

                SET @Sql = N'CREATE DATABASE ' + QUOTENAME(N'{escapedDatabaseLiteral}') +
                    N' ON PRIMARY (NAME = N''{escapedDatabaseLiteral}_data'', FILENAME = N''' +
                    REPLACE(@DataPath + N'{escapedDatabaseLiteral}_' + @Suffix + N'.mdf', N'''', N'''''') +
                    N''') LOG ON (NAME = N''{escapedDatabaseLiteral}_log'', FILENAME = N''' +
                    REPLACE(@LogPath + N'{escapedDatabaseLiteral}_' + @Suffix + N'_log.ldf', N'''', N'''''') +
                    N''')';

                EXEC sys.sp_executesql @Sql;
            END
            """;

        await using var command = new SqlCommand(sql, connection);
        await command.ExecuteNonQueryAsync();
    }

    private static async Task EnsureSchemaIsCurrentAsync(string databaseConnectionString)
    {
        await using var connection = new SqlConnection(databaseConnectionString);
        await connection.OpenAsync();

        var sql = """
            IF OBJECT_ID(N'dbo.AppSchemaVersion', N'U') IS NULL
            BEGIN
                CREATE TABLE dbo.AppSchemaVersion
                (
                    SchemaName NVARCHAR(100) NOT NULL CONSTRAINT PK_AppSchemaVersion PRIMARY KEY,
                    VersionNumber INT NOT NULL,
                    AppliedAt DATETIME2(0) NOT NULL
                );
            END;

            IF OBJECT_ID(N'dbo.Santizers', N'U') IS NULL
            BEGIN
                CREATE TABLE dbo.Santizers
                (
                    SanitizerID INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Santizers PRIMARY KEY,
                    MAC NVARCHAR(100) NOT NULL,
                    Location NVARCHAR(200) NULL,
                    CONSTRAINT UQ_Santizers_MAC UNIQUE (MAC)
                );
            END;
            ELSE
            BEGIN
                IF COL_LENGTH(N'dbo.Santizers', N'MAC') IS NULL
                BEGIN
                    ALTER TABLE dbo.Santizers ADD MAC NVARCHAR(100) NULL;
                END;

                IF COL_LENGTH(N'dbo.Santizers', N'Location') IS NULL
                BEGIN
                    ALTER TABLE dbo.Santizers ADD Location NVARCHAR(200) NULL;
                END;

                UPDATE dbo.Santizers
                SET MAC = CONCAT(N'UNKNOWN-', SanitizerID)
                WHERE MAC IS NULL OR LTRIM(RTRIM(MAC)) = N'';

                ALTER TABLE dbo.Santizers ALTER COLUMN MAC NVARCHAR(100) NOT NULL;

                IF NOT EXISTS
                (
                    SELECT 1
                    FROM sys.key_constraints
                    WHERE parent_object_id = OBJECT_ID(N'dbo.Santizers')
                        AND name = N'UQ_Santizers_MAC'
                )
                BEGIN
                    ALTER TABLE dbo.Santizers
                    ADD CONSTRAINT UQ_Santizers_MAC UNIQUE (MAC);
                END;
            END;

            IF OBJECT_ID(N'dbo.doctors', N'U') IS NULL
            BEGIN
                CREATE TABLE dbo.doctors
                (
                    DoctorID INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_doctors PRIMARY KEY,
                    DoctorName NVARCHAR(200) NULL,
                    DoctorRFIDTag NVARCHAR(100) NOT NULL,
                    CONSTRAINT UQ_doctors_DoctorRFIDTag UNIQUE (DoctorRFIDTag)
                );
            END;
            ELSE
            BEGIN
                IF COL_LENGTH(N'dbo.doctors', N'DoctorName') IS NULL
                BEGIN
                    ALTER TABLE dbo.doctors ADD DoctorName NVARCHAR(200) NULL;
                END;

                IF COL_LENGTH(N'dbo.doctors', N'DoctorRFIDTag') IS NULL
                BEGIN
                    ALTER TABLE dbo.doctors ADD DoctorRFIDTag NVARCHAR(100) NULL;
                END;

                UPDATE dbo.doctors
                SET DoctorRFIDTag = CONCAT(N'UNKNOWN-', DoctorID)
                WHERE DoctorRFIDTag IS NULL OR LTRIM(RTRIM(DoctorRFIDTag)) = N'';

                ALTER TABLE dbo.doctors ALTER COLUMN DoctorRFIDTag NVARCHAR(100) NOT NULL;

                IF NOT EXISTS
                (
                    SELECT 1
                    FROM sys.key_constraints
                    WHERE parent_object_id = OBJECT_ID(N'dbo.doctors')
                        AND name = N'UQ_doctors_DoctorRFIDTag'
                )
                BEGIN
                    ALTER TABLE dbo.doctors
                    ADD CONSTRAINT UQ_doctors_DoctorRFIDTag UNIQUE (DoctorRFIDTag);
                END;
            END;

            IF OBJECT_ID(N'dbo.SanitizationLog', N'U') IS NULL
            BEGIN
                CREATE TABLE dbo.SanitizationLog
                (
                    DoctorID INT NOT NULL,
                    SantizerID INT NOT NULL,
                    StartTime DATETIME2(0) NOT NULL,
                    Duration INT NOT NULL,
                    CONSTRAINT FK_SanitizationLog_doctors FOREIGN KEY (DoctorID)
                        REFERENCES dbo.doctors (DoctorID),
                    CONSTRAINT FK_SanitizationLog_Santizers FOREIGN KEY (SantizerID)
                        REFERENCES dbo.Santizers (SanitizerID)
                );

                CREATE INDEX IX_SanitizationLog_StartTime
                    ON dbo.SanitizationLog (StartTime);
            END;
            ELSE
            BEGIN
                IF COL_LENGTH(N'dbo.SanitizationLog', N'StartTime') IS NULL
                BEGIN
                    ALTER TABLE dbo.SanitizationLog ADD StartTime DATETIME2(0) NOT NULL
                        CONSTRAINT DF_SanitizationLog_StartTime DEFAULT SYSUTCDATETIME();
                END;

                IF COL_LENGTH(N'dbo.SanitizationLog', N'Duration') IS NULL
                BEGIN
                    ALTER TABLE dbo.SanitizationLog ADD Duration INT NOT NULL
                        CONSTRAINT DF_SanitizationLog_Duration DEFAULT 1;
                END;

                IF NOT EXISTS
                (
                    SELECT 1
                    FROM sys.indexes
                    WHERE object_id = OBJECT_ID(N'dbo.SanitizationLog')
                        AND name = N'IX_SanitizationLog_StartTime'
                )
                BEGIN
                    CREATE INDEX IX_SanitizationLog_StartTime
                        ON dbo.SanitizationLog (StartTime);
                END;
            END;

            IF OBJECT_ID(N'dbo.KpiSettings', N'U') IS NULL
            BEGIN
                CREATE TABLE dbo.KpiSettings
                (
                    SettingName NVARCHAR(100) NOT NULL CONSTRAINT PK_KpiSettings PRIMARY KEY,
                    SettingValue INT NOT NULL
                );
            END;

            IF NOT EXISTS (SELECT 1 FROM dbo.KpiSettings WHERE SettingName = N'MinimumDuration')
            BEGIN
                INSERT INTO dbo.KpiSettings (SettingName, SettingValue)
                VALUES (N'MinimumDuration', 20);
            END;
            """;

        await using var command = new SqlCommand(sql, connection);
        await command.ExecuteNonQueryAsync();

        await using var versionCommand = new SqlCommand("""
            UPDATE dbo.AppSchemaVersion
            SET VersionNumber = @VersionNumber,
                AppliedAt = SYSUTCDATETIME()
            WHERE SchemaName = N'SmartSanitizer';

            IF @@ROWCOUNT = 0
            BEGIN
                INSERT INTO dbo.AppSchemaVersion (SchemaName, VersionNumber, AppliedAt)
                VALUES (N'SmartSanitizer', @VersionNumber, SYSUTCDATETIME());
            END;
            """, connection);

        versionCommand.Parameters.Add("@VersionNumber", SqlDbType.Int).Value = CurrentSchemaVersion;
        await versionCommand.ExecuteNonQueryAsync();
    }
}

public sealed class ManagementService(IConfiguration configuration)
{
    public async Task<IReadOnlyList<DoctorDto>> GetDoctorsAsync(CancellationToken cancellationToken)
    {
        await using var connection = await OpenConnectionAsync(cancellationToken);
        await using var command = new SqlCommand("""
            SELECT DoctorID, DoctorName, DoctorRFIDTag
            FROM dbo.doctors
            ORDER BY DoctorName, DoctorID;
            """, connection);

        var doctors = new List<DoctorDto>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);

        while (await reader.ReadAsync(cancellationToken))
        {
            doctors.Add(new DoctorDto(
                reader.GetInt32(0),
                reader.IsDBNull(1) ? null : reader.GetString(1),
                reader.GetString(2)));
        }

        return doctors;
    }

    public async Task<DoctorDto> AddDoctorAsync(DoctorRequest request, CancellationToken cancellationToken)
    {
        await using var connection = await OpenConnectionAsync(cancellationToken);
        await using var command = new SqlCommand("""
            IF EXISTS (SELECT 1 FROM dbo.doctors WHERE DoctorRFIDTag = @DoctorRFIDTag)
            BEGIN
                UPDATE dbo.doctors
                SET DoctorName = @DoctorName
                WHERE DoctorRFIDTag = @DoctorRFIDTag;
            END
            ELSE
            BEGIN
                INSERT INTO dbo.doctors (DoctorName, DoctorRFIDTag)
                VALUES (@DoctorName, @DoctorRFIDTag);
            END;

            SELECT DoctorID, DoctorName, DoctorRFIDTag
            FROM dbo.doctors
            WHERE DoctorRFIDTag = @DoctorRFIDTag;
            """, connection);

        command.Parameters.Add("@DoctorName", SqlDbType.NVarChar, 200).Value = request.DoctorName.Trim();
        command.Parameters.Add("@DoctorRFIDTag", SqlDbType.NVarChar, 100).Value = request.DoctorRFIDTag.Trim();

        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        if (!await reader.ReadAsync(cancellationToken))
        {
            throw new InvalidOperationException("Doctor could not be saved.");
        }

        return new DoctorDto(reader.GetInt32(0), reader.IsDBNull(1) ? null : reader.GetString(1), reader.GetString(2));
    }

    public async Task<IReadOnlyList<SanitizerDto>> GetSanitizersAsync(CancellationToken cancellationToken)
    {
        await using var connection = await OpenConnectionAsync(cancellationToken);
        await using var command = new SqlCommand("""
            SELECT SanitizerID, MAC, Location
            FROM dbo.Santizers
            ORDER BY Location, SanitizerID;
            """, connection);

        var sanitizers = new List<SanitizerDto>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);

        while (await reader.ReadAsync(cancellationToken))
        {
            sanitizers.Add(new SanitizerDto(
                reader.GetInt32(0),
                reader.GetString(1),
                reader.IsDBNull(2) ? null : reader.GetString(2)));
        }

        return sanitizers;
    }

    public async Task<SanitizerDto> AddSanitizerAsync(SanitizerRequest request, CancellationToken cancellationToken)
    {
        await using var connection = await OpenConnectionAsync(cancellationToken);
        await using var command = new SqlCommand("""
            IF EXISTS (SELECT 1 FROM dbo.Santizers WHERE MAC = @MAC)
            BEGIN
                UPDATE dbo.Santizers
                SET Location = @Location
                WHERE MAC = @MAC;
            END
            ELSE
            BEGIN
                INSERT INTO dbo.Santizers (MAC, Location)
                VALUES (@MAC, @Location);
            END;

            SELECT SanitizerID, MAC, Location
            FROM dbo.Santizers
            WHERE MAC = @MAC;
            """, connection);

        command.Parameters.Add("@MAC", SqlDbType.NVarChar, 100).Value = request.MAC.Trim();
        command.Parameters.Add("@Location", SqlDbType.NVarChar, 200).Value =
            string.IsNullOrWhiteSpace(request.Location) ? DBNull.Value : request.Location.Trim();

        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        if (!await reader.ReadAsync(cancellationToken))
        {
            throw new InvalidOperationException("Sanitizer could not be saved.");
        }

        return new SanitizerDto(reader.GetInt32(0), reader.GetString(1), reader.IsDBNull(2) ? null : reader.GetString(2));
    }

    public async Task<IReadOnlyList<SanitizationLogDto>> GetSanitizationLogsAsync(CancellationToken cancellationToken)
    {
        await using var connection = await OpenConnectionAsync(cancellationToken);
        await using var command = new SqlCommand("""
            SELECT TOP (500)
                d.DoctorID,
                d.DoctorName,
                d.DoctorRFIDTag,
                s.SanitizerID,
                s.MAC,
                s.Location,
                l.StartTime,
                l.Duration
            FROM dbo.SanitizationLog l
            INNER JOIN dbo.doctors d ON d.DoctorID = l.DoctorID
            INNER JOIN dbo.Santizers s ON s.SanitizerID = l.SantizerID
            ORDER BY l.StartTime DESC;
            """, connection);

        var logs = new List<SanitizationLogDto>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);

        while (await reader.ReadAsync(cancellationToken))
        {
            logs.Add(new SanitizationLogDto(
                reader.GetInt32(0),
                reader.IsDBNull(1) ? null : reader.GetString(1),
                reader.GetString(2),
                reader.GetInt32(3),
                reader.GetString(4),
                reader.IsDBNull(5) ? null : reader.GetString(5),
                reader.GetDateTime(6),
                reader.GetInt32(7)));
        }

        return logs;
    }

    public async Task<int> GetMinimumDurationAsync(CancellationToken cancellationToken)
    {
        await using var connection = await OpenConnectionAsync(cancellationToken);
        await using var command = new SqlCommand("""
            SELECT SettingValue
            FROM dbo.KpiSettings
            WHERE SettingName = N'MinimumDuration';
            """, connection);

        var value = await command.ExecuteScalarAsync(cancellationToken);
        return value is null or DBNull ? 20 : Convert.ToInt32(value);
    }

    public async Task<int> SetMinimumDurationAsync(int minimumDuration, CancellationToken cancellationToken)
    {
        await using var connection = await OpenConnectionAsync(cancellationToken);
        await using var command = new SqlCommand("""
            UPDATE dbo.KpiSettings
            SET SettingValue = @MinimumDuration
            WHERE SettingName = N'MinimumDuration';

            IF @@ROWCOUNT = 0
            BEGIN
                INSERT INTO dbo.KpiSettings (SettingName, SettingValue)
                VALUES (N'MinimumDuration', @MinimumDuration);
            END;
            """, connection);

        command.Parameters.Add("@MinimumDuration", SqlDbType.Int).Value = minimumDuration;
        await command.ExecuteNonQueryAsync(cancellationToken);
        return minimumDuration;
    }

    public async Task<IReadOnlyList<DoctorPerformanceDto>> GetDoctorPerformanceAsync(CancellationToken cancellationToken)
    {
        var minimumDuration = await GetMinimumDurationAsync(cancellationToken);
        await using var connection = await OpenConnectionAsync(cancellationToken);
        await using var command = new SqlCommand("""
            SELECT
                d.DoctorID,
                d.DoctorName,
                d.DoctorRFIDTag,
                COUNT(l.DoctorID) AS TotalLogs,
                SUM(CASE WHEN l.Duration >= @MinimumDuration THEN 1 ELSE 0 END) AS SuccessfulLogs,
                AVG(CAST(l.Duration AS DECIMAL(10,2))) AS AverageDuration
            FROM dbo.doctors d
            LEFT JOIN dbo.SanitizationLog l ON l.DoctorID = d.DoctorID
            GROUP BY d.DoctorID, d.DoctorName, d.DoctorRFIDTag
            ORDER BY d.DoctorName, d.DoctorID;
            """, connection);

        command.Parameters.Add("@MinimumDuration", SqlDbType.Int).Value = minimumDuration;

        var performance = new List<DoctorPerformanceDto>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);

        while (await reader.ReadAsync(cancellationToken))
        {
            var totalLogs = reader.GetInt32(3);
            var successfulLogs = reader.IsDBNull(4) ? 0 : reader.GetInt32(4);
            var missedLogs = totalLogs - successfulLogs;
            var averageDuration = reader.IsDBNull(5) ? 0 : reader.GetDecimal(5);
            var compliancePercent = totalLogs == 0 ? 0 : Math.Round(successfulLogs * 100m / totalLogs, 2);

            performance.Add(new DoctorPerformanceDto(
                reader.GetInt32(0),
                reader.IsDBNull(1) ? null : reader.GetString(1),
                reader.GetString(2),
                totalLogs,
                successfulLogs,
                missedLogs,
                averageDuration,
                compliancePercent,
                minimumDuration));
        }

        return performance;
    }

    private async Task<SqlConnection> OpenConnectionAsync(CancellationToken cancellationToken)
    {
        var connectionString = configuration.GetConnectionString("SmartSanitizationDb")
            ?? throw new InvalidOperationException("Connection string 'SmartSanitizationDb' is missing.");

        var connection = new SqlConnection(connectionString);
        await connection.OpenAsync(cancellationToken);
        return connection;
    }
}

public sealed class SanitizationLogService(IConfiguration configuration)
{
    public async Task<SanitizationLogResponse> RegisterLogAsync(
        SanitizationLogRequest request,
        CancellationToken cancellationToken)
    {
        var connectionString = configuration.GetConnectionString("SmartSanitizationDb")
            ?? throw new InvalidOperationException("Connection string 'SmartSanitizationDb' is missing.");

        await using var connection = new SqlConnection(connectionString);
        await connection.OpenAsync(cancellationToken);

        await using var transaction = await connection.BeginTransactionAsync(IsolationLevel.Serializable, cancellationToken);

        try
        {
            var doctor = await GetOrCreateDoctorAsync(connection, (SqlTransaction)transaction, request.DoctorRFIDTag.Trim(), cancellationToken);
            var sanitizer = await GetOrCreateSanitizerAsync(connection, (SqlTransaction)transaction, request.SanitizerMAC.Trim(), cancellationToken);
            var startTime = DateTime.UtcNow;

            await using var insertLogCommand = new SqlCommand("""
                INSERT INTO dbo.SanitizationLog (DoctorID, SantizerID, StartTime, Duration)
                VALUES (@DoctorID, @SantizerID, @StartTime, @Duration);
                """, connection, (SqlTransaction)transaction);

            insertLogCommand.Parameters.Add("@DoctorID", SqlDbType.Int).Value = doctor.Id;
            insertLogCommand.Parameters.Add("@SantizerID", SqlDbType.Int).Value = sanitizer.Id;
            insertLogCommand.Parameters.Add("@StartTime", SqlDbType.DateTime2).Value = startTime;
            insertLogCommand.Parameters.Add("@Duration", SqlDbType.Int).Value = request.Duration;

            await insertLogCommand.ExecuteNonQueryAsync(cancellationToken);
            await transaction.CommitAsync(cancellationToken);

            return new SanitizationLogResponse(
                doctor.Id,
                sanitizer.Id,
                startTime,
                request.Duration,
                doctor.Created,
                sanitizer.Created);
        }
        catch
        {
            await transaction.RollbackAsync(cancellationToken);
            throw;
        }
    }

    private static async Task<(int Id, bool Created)> GetOrCreateDoctorAsync(
        SqlConnection connection,
        SqlTransaction transaction,
        string doctorRfidTag,
        CancellationToken cancellationToken)
    {
        await using var findCommand = new SqlCommand("""
            SELECT DoctorID
            FROM dbo.doctors WITH (UPDLOCK, HOLDLOCK)
            WHERE DoctorRFIDTag = @DoctorRFIDTag;
            """, connection, transaction);

        findCommand.Parameters.Add("@DoctorRFIDTag", SqlDbType.NVarChar, 100).Value = doctorRfidTag;
        var existingId = await findCommand.ExecuteScalarAsync(cancellationToken);

        if (existingId is not null && existingId != DBNull.Value)
        {
            return (Convert.ToInt32(existingId), false);
        }

        await using var insertCommand = new SqlCommand("""
            INSERT INTO dbo.doctors (DoctorName, DoctorRFIDTag)
            OUTPUT INSERTED.DoctorID
            VALUES (@DoctorName, @DoctorRFIDTag);
            """, connection, transaction);

        insertCommand.Parameters.Add("@DoctorName", SqlDbType.NVarChar, 200).Value = "Unknown";
        insertCommand.Parameters.Add("@DoctorRFIDTag", SqlDbType.NVarChar, 100).Value = doctorRfidTag;

        var newId = await insertCommand.ExecuteScalarAsync(cancellationToken);
        return (Convert.ToInt32(newId), true);
    }

    private static async Task<(int Id, bool Created)> GetOrCreateSanitizerAsync(
        SqlConnection connection,
        SqlTransaction transaction,
        string sanitizerMac,
        CancellationToken cancellationToken)
    {
        await using var findCommand = new SqlCommand("""
            SELECT SanitizerID
            FROM dbo.Santizers WITH (UPDLOCK, HOLDLOCK)
            WHERE MAC = @MAC;
            """, connection, transaction);

        findCommand.Parameters.Add("@MAC", SqlDbType.NVarChar, 100).Value = sanitizerMac;
        var existingId = await findCommand.ExecuteScalarAsync(cancellationToken);

        if (existingId is not null && existingId != DBNull.Value)
        {
            return (Convert.ToInt32(existingId), false);
        }

        await using var insertCommand = new SqlCommand("""
            INSERT INTO dbo.Santizers (MAC, Location)
            OUTPUT INSERTED.SanitizerID
            VALUES (@MAC, @Location);
            """, connection, transaction);

        insertCommand.Parameters.Add("@MAC", SqlDbType.NVarChar, 100).Value = sanitizerMac;
        insertCommand.Parameters.Add("@Location", SqlDbType.NVarChar, 200).Value = "Unknown";

        var newId = await insertCommand.ExecuteScalarAsync(cancellationToken);
        return (Convert.ToInt32(newId), true);
    }
}
