// Debounce date/time edits and ignore responses for selections no longer shown.
export function checkBookingAvailability(request, onResult, delay = 250) {
  let active = true;
  const controller = new AbortController();
  const timer = setTimeout(async () => {
    try {
      const result = await request(controller.signal);
      if (active) onResult({ available: result.available === true, message: result.message || "" });
    } catch (error) {
      if (active) onResult({ available: false, error: true, message: error.message || "Could not check this time. Please try again." });
    }
  }, delay);
  return () => { active = false; clearTimeout(timer); controller.abort(); };
}
