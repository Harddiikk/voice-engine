/**
 * Formatting for a campaign's calling window (its `schedule_config`).
 *
 * Campaigns dial only inside this window and auto-pause outside it, so the
 * window and the timezone it is evaluated in belong together — "09:00–20:00"
 * means nothing without knowing whose 8pm it is.
 */

export type ScheduleSlot = {
    day_of_week: number;
    start_time: string;
    end_time: string;
};

export type ScheduleConfig = {
    enabled?: boolean;
    timezone?: string | null;
    slots?: ScheduleSlot[] | null;
} | null | undefined;

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/**
 * Short label for a timezone — the abbreviation the zone is actually using
 * right now (IST, GMT+5:30, …) rather than the raw IANA name, which is too
 * long for a table cell.
 */
export function shortTimezone(timezone: string | null | undefined): string {
    if (!timezone) return '';
    try {
        const parts = new Intl.DateTimeFormat('en-US', {
            timeZone: timezone,
            timeZoneName: 'short',
        }).formatToParts(new Date());
        return parts.find((p) => p.type === 'timeZoneName')?.value ?? timezone;
    } catch {
        // Unknown/invalid zone — show what was stored rather than nothing.
        return timezone;
    }
}

/** True when every configured slot shares the same start and end time. */
function slotsShareOneWindow(slots: ScheduleSlot[]): boolean {
    return slots.every(
        (s) => s.start_time === slots[0].start_time && s.end_time === slots[0].end_time,
    );
}

/**
 * One-line summary of when a campaign is allowed to dial.
 *
 * Returns "Any time" when no schedule restricts it — that is genuinely the
 * behaviour, and saying nothing would read as "unknown".
 */
export function formatCallingWindow(schedule: ScheduleConfig): string {
    const slots = schedule?.slots ?? [];
    if (!schedule?.enabled || slots.length === 0) return 'Any time';

    const tz = shortTimezone(schedule.timezone);
    const suffix = tz ? ` ${tz}` : '';

    if (slotsShareOneWindow(slots)) {
        const { start_time, end_time } = slots[0];
        const days = slots.length === 7 ? '' : ` · ${describeDays(slots)}`;
        return `${start_time}–${end_time}${suffix}${days}`;
    }

    // Mixed per-day windows don't compress into one line; point at the detail
    // page instead of inventing a misleading summary.
    return `${slots.length} time slots${suffix}`;
}

/** "Mon–Fri" for a contiguous run, otherwise "Mon, Wed, Sat". */
function describeDays(slots: ScheduleSlot[]): string {
    const days = [...new Set(slots.map((s) => s.day_of_week))].sort((a, b) => a - b);
    if (days.length === 0) return '';

    const contiguous = days.every((d, i) => i === 0 || d === days[i - 1] + 1);
    if (contiguous && days.length > 2) {
        return `${DAY_LABELS[days[0]]}–${DAY_LABELS[days[days.length - 1]]}`;
    }
    return days.map((d) => DAY_LABELS[d] ?? d).join(', ');
}
