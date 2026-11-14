SELECT COUNT(*) AS value
FROM logs.workersLogs
WHERE accountTag = '{account_id}'
  AND scriptName = '{worker}'
  AND timestamp >= NOW() - INTERVAL '15' MINUTE
  AND message LIKE 'erp_operational_health:%'
