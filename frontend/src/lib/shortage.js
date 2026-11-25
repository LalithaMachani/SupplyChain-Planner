const shortageSeverities = ['critical', 'high', 'medium', 'low']

export function shortageKey(item) {
  return `${item.planning_run_id}-${item.sku_id}-${item.location_id}-${item.week_start}`
}

export function countShortagesBySeverity(shortages) {
  const counts = Object.fromEntries(shortageSeverities.map((severity) => [severity, 0]))
  for (const item of shortages) {
    if (Object.prototype.hasOwnProperty.call(counts, item.severity)) {
      counts[item.severity] += 1
    }
  }
  return counts
}
