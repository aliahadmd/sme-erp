SELECT COUNT(*) AS value
FROM logs.workersLogs
WHERE accountTag = '{account_id}'
  AND scriptName = '{worker}'
  AND timestamp >= NOW() - INTERVAL '15' MINUTE
  AND message IN ('erp_operational_health:critical',
    'erp_operational_health:warning', 'erp_operational_health:unavailable')
