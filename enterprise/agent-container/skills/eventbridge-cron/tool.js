#!/usr/bin/env node
/**
 * eventbridge-cron — Schedule recurring tasks via Amazon EventBridge Scheduler.
 *
 * Uses AWS CLI via execFileSync (argument arrays, no shell) to prevent injection.
 *
 * Actions:
 *   create:  { "action":"create", "cron_expression":"cron(0 9 * * ? *)", "timezone":"Asia/Shanghai", "message":"Check email", "schedule_name":"Daily email" }
 *   list:    { "action":"list" }
 *   update:  { "action":"update", "schedule_id":"a1b2c3d4", "expression":"cron(0 10 * * ? *)", "timezone":"...", "message":"...", "name":"...", "enable":true, "disable":false }
 *   delete:  { "action":"delete", "schedule_id":"a1b2c3d4" }
 *
 * Required env vars:
 *   AWS_REGION, EVENTBRIDGE_SCHEDULE_GROUP, CRON_LAMBDA_ARN,
 *   EVENTBRIDGE_ROLE_ARN, DYNAMODB_TABLE (or IDENTITY_TABLE_NAME)
 */

'use strict';
const { execFileSync } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');

const REGION = process.env.AWS_REGION || 'us-east-1';
const SCHEDULE_GROUP = process.env.EVENTBRIDGE_SCHEDULE_GROUP || 'openclaw-cron';
const CRON_LAMBDA_ARN = process.env.CRON_LAMBDA_ARN;
const EVENTBRIDGE_ROLE_ARN = process.env.EVENTBRIDGE_ROLE_ARN;
const TABLE_NAME = process.env.IDENTITY_TABLE_NAME || process.env.DYNAMODB_TABLE;

// Resolve tenant ID from /tmp/base_tenant_id (written by server.py), not env var
let TENANT_ID = process.env.TENANT_ID || 'unknown';
try {
  const base = fs.readFileSync('/tmp/base_tenant_id', 'utf8').trim();
  if (base && base !== 'unknown') TENANT_ID = base;
} catch {}
if (TENANT_ID === 'unknown') {
  try {
    const full = fs.readFileSync('/tmp/tenant_id', 'utf8').trim();
    const parts = full.split('__');
    if (parts.length >= 2 && parts[1] !== 'unknown') TENANT_ID = parts[1];
  } catch {}
}

let args = {};
try { args = JSON.parse(process.argv[2] || '{}'); } catch { args = {}; }
const action = args.action || 'list';

// --- CLI helpers (execFileSync — no shell, no injection) ---

function aws(service, ...cmdArgs) {
  return execFileSync('aws', [service, ...cmdArgs, '--region', REGION], {
    encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'], timeout: 30000,
  }).trim();
}

function awsJson(service, ...cmdArgs) {
  return JSON.parse(aws(service, ...cmdArgs, '--output', 'json'));
}

function fail(msg) {
  console.log(JSON.stringify({ error: msg }));
  process.exit(1);
}

// --- Validation ---

function validateEnv() {
  const missing = [];
  if (!CRON_LAMBDA_ARN) missing.push('CRON_LAMBDA_ARN');
  if (!EVENTBRIDGE_ROLE_ARN) missing.push('EVENTBRIDGE_ROLE_ARN');
  if (!TABLE_NAME) missing.push('DYNAMODB_TABLE or IDENTITY_TABLE_NAME');
  if (missing.length) fail(`Missing env vars: ${missing.join(', ')}`);
}

function validateExpression(expression) {
  const cronRegex = /^cron\((.+)\)$/;
  const rateRegex = /^rate\(\d+\s+(minute|minutes|hour|hours|day|days)\)$/;
  const atRegex = /^at\((\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\)$/;
  if (atRegex.test(expression)) {
    if (isNaN(new Date(expression.match(atRegex)[1]).getTime())) return 'Invalid at() datetime';
    return null;
  }
  if (rateRegex.test(expression)) {
    const m = expression.match(/^rate\((\d+)\s+(minute|minutes)\)$/);
    if (m && parseInt(m[1], 10) < 5) return 'Minimum rate interval is 5 minutes';
    return null;
  }
  const cronMatch = expression.match(cronRegex);
  if (cronMatch) {
    const fields = cronMatch[1].trim().split(/\s+/);
    if (fields.length !== 6) return `cron() must have 6 fields, got ${fields.length}`;
    if (fields[0] === '*' || fields[0] === '*/1') return 'Every-minute cron not allowed. Use */5 or higher';
    return null;
  }
  return `Invalid expression "${expression}". Must be cron(...), rate(...), or at(...)`;
}

function validateTimezone(tz) {
  try { Intl.DateTimeFormat(undefined, { timeZone: tz }); return null; }
  catch { return `Invalid timezone "${tz}"`; }
}

// --- DynamoDB helpers (execFileSync, no shell) ---

function ddbPutCronRecord(userId, record) {
  const item = {
    PK: { S: `USER#${userId}` },
    SK: { S: `CRON#${record.scheduleId}` },
    scheduleId: { S: record.scheduleId },
    scheduleName: { S: record.scheduleName || '' },
    expression: { S: record.expression },
    timezone: { S: record.timezone },
    message: { S: record.message },
    channel: { S: record.channel || 'portal' },
    channelTarget: { S: record.channelTarget || userId },
    actorId: { S: record.actorId || `portal:${userId}` },
    enabled: { BOOL: record.enabled !== false },
    createdAt: { S: record.createdAt || new Date().toISOString() },
    updatedAt: { S: record.updatedAt || new Date().toISOString() },
  };
  aws('dynamodb', 'put-item', '--table-name', TABLE_NAME, '--item', JSON.stringify(item));
}

function ddbGetCronRecord(userId, scheduleId) {
  try {
    const key = JSON.stringify({ PK: { S: `USER#${userId}` }, SK: { S: `CRON#${scheduleId}` } });
    const resp = awsJson('dynamodb', 'get-item', '--table-name', TABLE_NAME, '--key', key);
    if (!resp.Item) return null;
    const i = resp.Item;
    return {
      scheduleId: (i.scheduleId || {}).S || '',
      scheduleName: (i.scheduleName || {}).S || '',
      expression: (i.expression || {}).S || '',
      timezone: (i.timezone || {}).S || '',
      message: (i.message || {}).S || '',
      channel: (i.channel || {}).S || '',
      channelTarget: (i.channelTarget || {}).S || '',
      enabled: (i.enabled || {}).BOOL !== false,
      createdAt: (i.createdAt || {}).S || '',
    };
  } catch { return null; }
}

function ddbDeleteCronRecord(userId, scheduleId) {
  const key = JSON.stringify({ PK: { S: `USER#${userId}` }, SK: { S: `CRON#${scheduleId}` } });
  try { aws('dynamodb', 'delete-item', '--table-name', TABLE_NAME, '--key', key); } catch {}
}

function ddbListCronRecords(userId) {
  try {
    const expr = JSON.stringify({ ':pk': { S: `USER#${userId}` }, ':sk': { S: 'CRON#' } });
    const resp = awsJson('dynamodb', 'query', '--table-name', TABLE_NAME,
      '--key-condition-expression', 'PK = :pk AND begins_with(SK, :sk)',
      '--expression-attribute-values', expr);
    return (resp.Items || []).map(i => ({
      scheduleId: (i.scheduleId || {}).S || '',
      scheduleName: (i.scheduleName || {}).S || '',
      expression: (i.expression || {}).S || '',
      timezone: (i.timezone || {}).S || '',
      message: (i.message || {}).S || '',
      enabled: (i.enabled || {}).BOOL !== false,
      createdAt: (i.createdAt || {}).S || '',
    }));
  } catch { return []; }
}

// --- Helpers ---

function generateScheduleId() { return crypto.randomBytes(4).toString('hex'); }

function buildScheduleName(userId, scheduleId) {
  const prefix = 'openclaw-';
  const suffix = `-${scheduleId}`;
  const maxLen = 64 - prefix.length - suffix.length;
  return `${prefix}${userId.slice(0, maxLen)}${suffix}`;
}

const DAY_NAMES = {
  SUN: 'Sunday', MON: 'Monday', TUE: 'Tuesday', WED: 'Wednesday',
  THU: 'Thursday', FRI: 'Friday', SAT: 'Saturday',
};

function describeSchedule(expression, timezone) {
  const tz = timezone || 'UTC';
  const rateMatch = expression.match(/^rate\((\d+)\s+(\w+)\)$/);
  if (rateMatch) return `every ${rateMatch[1]} ${rateMatch[2]}`;
  const atMatch = expression.match(/^at\((.+)\)$/);
  if (atMatch) return `once at ${atMatch[1]} ${tz}`;
  const cronMatch = expression.match(/^cron\((.+)\)$/);
  if (cronMatch) {
    const f = cronMatch[1].trim().split(/\s+/);
    if (f.length !== 6) return `${expression} ${tz}`;
    const time = `${f[1].padStart(2, '0')}:${f[0].padStart(2, '0')}`;
    if (f[2] === '?' && /^[A-Z]{3}-[A-Z]{3}$/.test(f[4])) {
      const [s, e] = f[4].split('-');
      return `${DAY_NAMES[s] || s} to ${DAY_NAMES[e] || e} at ${time} ${tz}`;
    }
    if (f[2] === '?' && f[4] !== '*') return `every ${DAY_NAMES[f[4].toUpperCase()] || f[4]} at ${time} ${tz}`;
    if ((f[2] === '?' || f[2] === '*') && (f[4] === '*' || f[4] === '?')) return `daily at ${time} ${tz}`;
    return `${expression} (${tz})`;
  }
  return `${expression} (${tz})`;
}

// --- Actions ---

function doCreate() {
  validateEnv();
  const { cron_expression, timezone, message, schedule_name, channel, channel_target } = args;
  if (!cron_expression) return fail('cron_expression is required');
  if (!timezone) return fail('timezone is required');
  if (!message) return fail('message is required');
  let err = validateExpression(cron_expression); if (err) return fail(err);
  err = validateTimezone(timezone); if (err) return fail(err);

  const userId = TENANT_ID;
  const scheduleId = generateScheduleId();
  const ebName = buildScheduleName(userId, scheduleId);
  const ch = channel || 'portal';
  const chTarget = channel_target || userId;

  const lambdaInput = JSON.stringify({
    userId, actorId: `${ch}:${chTarget}`, channel: ch, channelTarget: chTarget,
    message, scheduleId, scheduleName: schedule_name || `Schedule ${scheduleId}`,
  });

  const targetJson = JSON.stringify({ Arn: CRON_LAMBDA_ARN, RoleArn: EVENTBRIDGE_ROLE_ARN, Input: lambdaInput });
  try {
    aws('scheduler', 'create-schedule',
      '--name', ebName, '--group-name', SCHEDULE_GROUP,
      '--schedule-expression', cron_expression,
      '--schedule-expression-timezone', timezone,
      '--flexible-time-window', '{"Mode":"OFF"}',
      '--state', 'ENABLED',
      '--target', targetJson);
  } catch (e) { return fail(`EventBridge create failed: ${e.message || e}`); }

  const now = new Date().toISOString();
  try {
    ddbPutCronRecord(userId, {
      scheduleId, scheduleName: schedule_name || `Schedule ${scheduleId}`,
      expression: cron_expression, timezone, message,
      channel: ch, channelTarget: chTarget, actorId: `${ch}:${chTarget}`,
      enabled: true, createdAt: now, updatedAt: now,
    });
  } catch (e) {
    try { aws('scheduler', 'delete-schedule', '--name', ebName, '--group-name', SCHEDULE_GROUP); } catch {}
    return fail(`DynamoDB save failed (schedule rolled back): ${e.message || e}`);
  }

  console.log(JSON.stringify({
    action: 'created', scheduleId, scheduleName: schedule_name || `Schedule ${scheduleId}`,
    expression: cron_expression, timezone, message, channel: `${ch}:${chTarget}`,
  }, null, 2));
}

function doList() {
  const records = ddbListCronRecords(TENANT_ID);
  if (!records.length) {
    console.log(JSON.stringify({ action: 'list', count: 0, schedules: [], message: 'No scheduled tasks found.' }));
    return;
  }
  const schedules = records.map(r => ({
    id: r.scheduleId, name: r.scheduleName,
    schedule: describeSchedule(r.expression, r.timezone),
    raw: r.expression, message: r.message,
    status: r.enabled !== false ? 'ENABLED' : 'DISABLED',
    created: r.createdAt,
  }));
  console.log(JSON.stringify({ action: 'list', count: schedules.length, schedules }, null, 2));
}

function doUpdate() {
  validateEnv();
  const { schedule_id } = args;
  if (!schedule_id) return fail('schedule_id is required');
  const userId = TENANT_ID;
  const record = ddbGetCronRecord(userId, schedule_id);
  if (!record) return fail(`Schedule ${schedule_id} not found`);

  if (args.expression) { const e = validateExpression(args.expression); if (e) return fail(e); }
  if (args.timezone) { const e = validateTimezone(args.timezone); if (e) return fail(e); }

  const ebName = buildScheduleName(userId, schedule_id);

  let current;
  try { current = awsJson('scheduler', 'get-schedule', '--name', ebName, '--group-name', SCHEDULE_GROUP); }
  catch (e) { return fail(`Schedule not found in EventBridge: ${e.message || e}`); }

  const currentInput = JSON.parse(current.Target.Input || '{}');
  if (args.message) currentInput.message = args.message;
  if (args.name) currentInput.scheduleName = args.name;

  const newExpr = args.expression || current.ScheduleExpression;
  const newTz = args.timezone || current.ScheduleExpressionTimezone;
  const newState = args.disable === true ? 'DISABLED' : 'ENABLED';
  const targetJson = JSON.stringify({ Arn: CRON_LAMBDA_ARN, RoleArn: EVENTBRIDGE_ROLE_ARN, Input: JSON.stringify(currentInput) });

  try {
    aws('scheduler', 'update-schedule',
      '--name', ebName, '--group-name', SCHEDULE_GROUP,
      '--schedule-expression', newExpr,
      '--schedule-expression-timezone', newTz,
      '--flexible-time-window', '{"Mode":"OFF"}',
      '--state', newState,
      '--target', targetJson);
  } catch (e) { return fail(`EventBridge update failed: ${e.message || e}`); }

  const updates = {};
  if (args.expression) updates.expression = args.expression;
  if (args.timezone) updates.timezone = args.timezone;
  if (args.message) updates.message = args.message;
  if (args.name) updates.scheduleName = args.name;
  if (args.enable === true) updates.enabled = true;
  if (args.disable === true) updates.enabled = false;

  try {
    ddbPutCronRecord(userId, { ...record, ...updates, updatedAt: new Date().toISOString() });
  } catch (e) { /* non-fatal */ }

  console.log(JSON.stringify({ action: 'updated', scheduleId: schedule_id, updates }, null, 2));
}

function doDelete() {
  validateEnv();
  const { schedule_id } = args;
  if (!schedule_id) return fail('schedule_id is required');
  const userId = TENANT_ID;
  const record = ddbGetCronRecord(userId, schedule_id);
  if (!record) return fail(`Schedule ${schedule_id} not found`);

  const ebName = buildScheduleName(userId, schedule_id);
  try { aws('scheduler', 'delete-schedule', '--name', ebName, '--group-name', SCHEDULE_GROUP); }
  catch {} // Ignore if already deleted

  ddbDeleteCronRecord(userId, schedule_id);
  console.log(JSON.stringify({ action: 'deleted', scheduleId: schedule_id, name: record.scheduleName, expression: record.expression }, null, 2));
}

// --- Dispatch ---
try {
  switch (action) {
    case 'create': doCreate(); break;
    case 'list':   doList();   break;
    case 'update': doUpdate(); break;
    case 'delete': doDelete(); break;
    default: fail(`Unknown action: ${action}. Valid: create, list, update, delete`);
  }
} catch (e) { fail(e.message || String(e)); }
