/* What the cat says. Every line here is either a fixed explanation of how Kitty works, or a figure the
   page was already given by its own server. Nothing is estimated, and nothing is made up to fill a gap:
   when a figure isn't known, she says so and points at where it will appear.

   Trail and copy use "confirm", the word the rest of the product uses for a person saying money arrived. */
(function () {
  const POT = [
    "On a Pot trip one person holds the group's money. Everyone pays that person, and spending comes out of the pot.",
    "A payment counts only once the person holding the money confirms it arrived. Kitty never moves money, and neither do I.",
  ];
  const SHARED = [
    "On a Shared Expenses trip everyone pays for different things as you go.",
    "Each person's own agent finds what they paid, they release it to the group, and Kitty works out the fewest payments to settle up.",
  ];
  const PRIVATE = [
    "Your statement stays in your own folder. Your friends and the clerk never get the file.",
    "The group sees date, amount, category and who shared it, and only for what you release. Merchant names stay with you.",
    "One honest limit: whoever runs this Kitty server can read every room, including private ones.",
  ];
  const CONFIRM = [
    "A member says what they paid and releases it. It shows in the group as not counted yet.",
    "The treasurer's agent looks for it on the treasurer's own statement, in plain code with no model. That result is a possible match and nothing more.",
    "Only the treasurer's own press of Confirm makes it count. The clerk then posts it, and the figures change for everyone.",
  ];
  const STATEMENT_WALL = [
    "Because the group doesn't need it. To settle up they need amounts and dates, not your other spending.",
    "Your agent reads your statement on your side, and you choose which lines become chits. Nothing reaches the group until you release it.",
  ];

  const STEPS = ["Create trip", "Add members", "Submit claims", "Confirm payments", "Settle up"];
  /* Which step a trip has really reached, from what its board says. */
  function tripStep(t) {
    if (t.state !== "live") return 1;
    const b = t.board;
    if (!b) return 2;
    if (b.pot) {
      if (b.pot.square) return 5;
      if (!b.pot.contributions.length) return 2;
      if (b.pot.open || b.pot.roll.some(r => r.outstanding)) return 3;
      return 4;
    }
    const counted = b.expenses.filter(e => e.status === "released").length;
    if (!counted) return 2;
    if (b.transfers.some(x => ["awaiting_receiver", "challenged"].includes(x.status)) || b.held_count) return 3;
    return b.balances.every(x => x.paise === 0) ? 5 : 4;
  }
  function trail(step, sub = {}) {
    return `<ol class="trail ${step < 0 ? "list" : ""}" aria-label="${step < 0 ? "The steps of a trip" : "Where this trip has got to"}">${STEPS.map((s, i) => {
      const at = step < 0 ? "next" : i < step ? "done" : i === step ? "now" : "next";
      return `<li data-at="${at}" ${at === "now" ? 'aria-current="step"' : ""}>${at === "now" ? `<kitty-cat class="tiny" mood="${sub.mood || "idle"}"></kitty-cat>` : ""}<i class="dot"></i>${s}${sub[i] ? `<small>${sub[i]}</small>` : ""}</li>`;
    }).join("")}</ol>`;
  }
  function potTotals(pot) {
    const sum = k => pot.roll.reduce((a, r) => a + r[k], 0);
    return { asked: sum("asked"), credited: sum("credited"), outstanding: sum("outstanding"), above: sum("above") };
  }

  /* ---- home app ---- */
  function home(trips, nav) {
    return {
      id: "home", title: trips.length ? "Welcome back" : "Hi, I'm Kitty", mood: "idle", hello: true,
      say: trips.length ? "Pick a trip, or start another. I'll explain whatever's on screen." : "I help a group settle a trip without anyone showing their bank statement. Want to start one?",
      actions: [
        { label: "Help me set up my first trip", answer: ["Three steps: name the trip and its dates, add who was on it, then choose how the money works.", "Then you hand each person their own link. Each link opens once."], then: { label: "Start a trip", go: () => nav("/trips/new") } },
        { label: "Explain Kitty", answer: ["Everyone's own agent reads their own statement. The group sees only what each person chooses to release, and Kitty works out who owes whom.", "Kitty never moves money. You still pay each other in your usual apps."] },
        { label: "Show me how it works", answer: "Five steps, from creating a trip to settling up.", point: "#how" },
        { label: "Explain The Pot", answer: POT },
        { label: "What stays private?", answer: PRIVATE },
      ],
    };
  }
  function newTrip() {
    return {
      id: "new", title: "Starting a trip", mood: "guiding", hello: "curious",
      say: "Give it a name and the dates. You'll choose Shared Expenses or The Pot two steps from now.",
      actions: [
        { label: "Why do the dates matter?", answer: "Each person's agent looks only at spending inside these dates. Everything else on their statement gets ignored." },
        { label: "Why one currency?", answer: "Kitty doesn't convert currencies, so everyone's statement needs to be in the one you choose here." },
        { label: "Shared Expenses or The Pot?", answer: [...SHARED, ...POT] },
      ],
    };
  }
  function people(t) {
    const n = t.people.length;
    return {
      id: "people", title: "Who was on the trip?", mood: n < t.limits.min ? "guiding" : "idle",
      say: n < t.limits.min ? `A trip needs at least ${t.limits.min} people. You've added ${n}.` : `${n} people so far, up to ${t.limits.max}. Continue whenever everyone's in.`,
      actions: [
        { label: "What's the second name for?", answer: "It's how they appear on other people's statements, like UPI/DR/ASHA M. Their agent uses it to spot payments between friends." },
        { label: "Can I change this later?", answer: "Until you open the trip, yes. Once the group room opens, the people and dates are fixed." },
      ],
    };
  }
  function review(t) {
    return {
      id: "review", title: "Choosing how the money works", mood: "curious",
      say: `Last step for ${t.name}: does someone hold the group's money, or does everyone pay as they go?`,
      actions: [
        { label: "Explain The Pot", answer: POT },
        { label: "Explain Shared Expenses", answer: SHARED },
        { label: "Who can read what?", answer: PRIVATE },
      ],
    };
  }
  function building(t) {
    const failed = t.state === "failed";
    return {
      id: failed ? "stuck" : "building", title: failed ? "Set-up stopped" : "Opening the trip", mood: failed ? "warning" : "thinking",
      say: failed ? "Set-up stopped partway. What's already built is safe, and Try again carries on from there." : "I'm waiting on set-up with you. It usually takes about a minute, and you can leave this page.",
      actions: failed ? [{ label: "Show me what to press", answer: "Try again carries on from where it stopped.", point: "#retry" }] : [],
    };
  }
  function live(t, money) {
    const b = t.board, joined = t.people.filter(p => p.joined).length, stmts = t.people.filter(p => p.statement_added).length;
    const facts = [["Joined", `${joined} of ${t.people.length}`], ["Statements added", `${stmts} of ${t.people.length}`]];
    const step = tripStep(t);
    let say = "Send each person their own link. It opens once.";
    if (!t.apps_up) say = "This trip's apps aren't running. Start them again and everyone carries on from where they were.";
    else if (joined === t.people.length && step === 2) say = "Everyone's in. Nothing's been counted yet.";
    else if (step === 3) say = "Claims are in. They count only once they're confirmed.";
    else if (step === 4) say = "Claims are confirmed. Here's what's left to settle.";
    else if (step === 5) say = "Everyone's square.";
    const actions = [
      { label: "What do I do now?", answer: t.apps_up ? "Copy each person's link and send it to them privately. After that it's up to them: you can see who has joined, and nothing from anyone's statement." : "Press Start the apps.", point: t.people.some(p => p.invite_url) ? "[data-copy]" : t.apps_up ? undefined : "#restart" },
      { label: "Who hasn't joined yet?", answer: joined === t.people.length ? "Everyone has joined." : t.people.filter(p => !p.joined).map(p => p.name).join(", ") + "." },
    ];
    if (b && b.pot) {
      const x = potTotals(b.pot), c = b.trip.currency;
      facts.push(["Confirmed", money(x.credited, c)], ["Still to come", money(x.outstanding, c)]);
      actions.push({ label: "How much is still outstanding?", answer: x.asked ? `${money(x.outstanding, t.currency)} of the ${money(x.asked, t.currency)} asked for.` : "Nobody's been asked for money yet." });
    }
    actions.push({ label: "What can I see as organiser?", answer: "Who has joined and who has added a statement, as yes or no. You can't see what's in anyone's statement." });
    return { id: "live", title: t.name, mood: !t.apps_up ? "warning" : step === 5 ? "reconciled" : step === 3 ? "waiting" : "idle", say, facts, actions };
  }

  /* ---- a person's own app ---- */
  function person(d, tab, cash) {
    const asks = [];
    if (tab === "statement") {
      const st = d.statement;
      return { id: "statement", title: "Your statement", mood: st && st.state === "none" ? "guiding" : "idle", hello: "curious",
        say: st && st.state === "none" ? "Add a made-up CSV. It's checked right here and stays in your own folder." : "Your file is here. Nothing has gone anywhere you didn't choose.",
        actions: [
          { label: "Why can't the group see my statement?", answer: STATEMENT_WALL },
          { label: "What goes to the model provider?", answer: "Only spending lines from the trip's dates, with long numbers masked, and only once you press Go through my statement. Open See exactly what the model provider would get to read them. Balances, account numbers and references never go." },
          { label: "What can the organiser see?", answer: "That you've added a statement, as yes or no, and nothing from it." },
        ] };
    }
    if (tab === "private") {
      const last = d.private[d.private.length - 1];
      return { id: "private", title: "Just you", mood: last && last.mine ? "thinking" : "idle",
        say: last && last.mine ? "Your agent's working on that. It answers here." : "This room is you and your agent. I'm not your agent: I only explain the screen.",
        actions: [
          { label: "Who's in this room?", answer: ["You and your agent. The clerk can't get in, and neither can your friends.", "One honest limit: whoever runs this Kitty server can read every room."] },
          { label: "What's the difference between you and my agent?", answer: "Your agent reads your statement and files chits for you. I'm only the guide: I read what this page shows, and I can't read your statement or send anything." },
          { label: "Why can't the group see my statement?", answer: STATEMENT_WALL },
        ] };
    }
    if (tab === "group") return groupGuide(d, cash);
    return deskGuide(d, cash, asks);
  }

  function deskGuide(d, cash) {
    const slips = d.desk.slips;
    if (d.pot && d.pot.is_treasurer) {
      const open = slips.filter(s => s.kind === "pot_review" && s.state === "waiting");
      const found = open.filter(s => s.candidate === "found it").length;
      const differs = open.filter(s => s.candidate === "a different amount").length;
      const none = open.filter(s => s.candidate === "nothing yet").length;
      const unchecked = open.filter(s => s.candidate === "not checked yet").length;
      const asked = d.pot.roll.reduce((a, r) => a + r.asked, 0), left = d.pot.roll.reduce((a, r) => a + r.outstanding, 0);
      const decided = slips.filter(s => s.kind === "pot_review" && s.state === "confirmed").length;
      const why = [];
      if (differs) why.push(`${differs} ${differs === 1 ? "payment has" : "payments have"} a line near that date for a different amount. Kitty won't let that be confirmed: ask them to withdraw it and claim the right amount.`);
      if (none) why.push(`${none} ${none === 1 ? "payment has" : "payments have"} no line on your statement yet. Your export may end before the payment. Add a later one and press Check again, or confirm by hand if your bank app shows it.`);
      if (unchecked) why.push(`${unchecked} ${unchecked === 1 ? "payment hasn't" : "payments haven't"} been reported on by your agent yet.`);
      return {
        id: "treasurer", title: "Your review", mood: open.length ? "reviewing" : d.pot.roll.length && asked && !left ? "reconciled" : "idle",
        say: open.length ? `${open.length} ${open.length === 1 ? "payment needs" : "payments need"} you. ${found ? `${found} ${found === 1 ? "looks like a match" : "look like matches"}. ` : ""}Nothing counts until you confirm it.` : asked ? "Nothing's waiting on you." : "Nobody's been asked for money yet. Start with Ask for money.",
        facts: [["Waiting on you", String(open.length)], ["Possible matches", String(found)], ["Confirmed", String(decided)], ["Still to come", cash(left)]],
        actions: [
          { label: "Show me what needs my attention", answer: open.length ? `${open.length} waiting. I've moved you to the first one.` : "Nothing's waiting on you right now.", point: open.length ? ".review-open" : undefined, mood: open.length ? "guiding" : "curious" },
          { label: "Why is this payment unmatched?", answer: why.length ? why : "Nothing's unmatched right now. Every open payment has a possible match on your statement." },
          { label: "What do these statuses mean?", answer: ["Possible match: your agent found a line for that amount near that date. It isn't counted yet.", "Different amount, or nothing found: no match to confirm against.", "Pending: waiting for your decision.", "Confirmed: you said it arrived, the group can see that, and it counts."] },
          { label: "How do payment confirmations work?", answer: CONFIRM },
          { label: "How much is still outstanding?", answer: asked ? `${cash(left)} of the ${cash(asked)} you've asked for.` : "You haven't asked for any money yet." },
        ],
      };
    }
    if (d.pot) {
      const m = d.pot.mine, mine = slips.filter(s => s.kind === "contribution");
      const unreleased = mine.filter(s => s.state === "waiting").length;
      const trouble = mine.filter(s => ["needs_clarification", "rejected"].includes(s.state)).length;
      const pending = mine.filter(s => s.state === "submitted").length;
      const notes = mine.filter(s => s.state === "needs_clarification").map(s => `${s.amount}: ${d.pot.treasurer} can't confirm it yet.${s.note ? " " + s.note : ""}`);
      const say = trouble ? `${d.pot.treasurer} has a question about a payment of yours.` : unreleased ? "You've got a chit to release. Nothing's in the group until you do." : pending ? `Waiting for ${d.pot.treasurer} to confirm. It isn't counted yet.` : m.asked && !m.outstanding ? `You're all paid in. ${d.pot.treasurer} has confirmed it.` : m.outstanding ? `Paid it? Say so below, and ${d.pot.treasurer} confirms it arrived.` : "Nobody's asked for money yet.";
      return {
        id: "member", title: "Your contribution", mood: trouble ? "curious" : pending ? "waiting" : unreleased ? "filing" : m.asked && !m.outstanding ? "reconciled" : "idle",
        say, facts: [["Asked", cash(m.asked)], ["Confirmed", cash(m.credited)], ["Outstanding", cash(m.outstanding)]],
        actions: [
          { label: "Show me what needs my attention", answer: trouble ? (notes.length ? notes : "One of your payments wasn't counted.") : unreleased ? "A chit's waiting for you to release it." : pending ? `Nothing for you to do. ${d.pot.treasurer} has it.` : "Nothing's waiting on you.", point: trouble ? ".chit.alert" : unreleased ? ".chit.fresh" : undefined },
          { label: "How do I submit a payment?", answer: "Pay in your usual app. Then fill in I paid, with the amount and the date. It comes back as a chit for you to release.", point: "form[data-pot=paid]", mood: "filing" },
          { label: "What do these statuses mean?", answer: [`Waiting for ${d.pot.treasurer}: released, and not counted yet.`, `Confirmed: ${d.pot.treasurer} said it arrived, and it counts.`, `Can't confirm yet: ${d.pot.treasurer} has a question. You'll see it on the chit.`, "Not received: it isn't counted. Withdraw it if the payment failed."] },
          { label: "How do payment confirmations work?", answer: CONFIRM },
          { label: "Why can't the group see my statement?", answer: STATEMENT_WALL },
        ],
      };
    }
    const waiting = slips.filter(s => s.state === "waiting").length, stuck = slips.filter(s => ["challenged", "conflict"].includes(s.state)).length;
    return {
      id: "desk", title: "Your desk", mood: stuck ? "warning" : waiting ? "filing" : "idle",
      say: stuck ? "Something here needs a look." : waiting ? `${waiting} ${waiting === 1 ? "chit is" : "chits are"} waiting. Release it to share it, or drop it to keep it private.` : "Your agent's chits wait here for you. Nothing goes further until you release it.",
      facts: [["Waiting for you", String(waiting)]],
      actions: [
        { label: "Show me what needs my attention", answer: stuck ? "This one isn't counting until it's sorted out." : waiting ? "This one's waiting for you." : "Nothing's waiting on you.", point: stuck ? ".chit.alert" : waiting ? ".chit.fresh" : undefined },
        { label: "What does the clerk see?", answer: "Exactly what's under See exactly what the clerk receives, at the bottom of this desk. No merchant names." },
        { label: "Why can't the group see my statement?", answer: STATEMENT_WALL },
      ],
    };
  }

  function lastSettle(d) {
    const notes = d.group.filter(f => f.type === "note" && f.clerk !== false);
    return [...notes].reverse().find(f => /^Suggested|^Nothing to settle/.test(f.text));
  }
  function groupGuide(d, cash) {
    const square = d.group.some(f => f.type === "note" && f.clerk !== false && /Everyone's square/.test(f.text));
    const settle = lastSettle(d);
    const facts = [];
    let left = null, asked = null;
    if (d.pot) {
      asked = d.pot.roll.reduce((a, r) => a + r.asked, 0); left = d.pot.roll.reduce((a, r) => a + r.outstanding, 0);
      facts.push(["Asked for", cash(asked)], ["Confirmed", cash(d.pot.roll.reduce((a, r) => a + r.credited, 0))], ["Still to come", cash(left)]);
    }
    const settleWords = settle ? settle.text.replace(/\*\*/g, "") : null;
    return {
      id: "group", title: "The group", mood: square ? "reconciled" : "idle",
      say: square ? "Everyone's square. Every balance is zero." : "Everyone on the trip can see this room. Nothing private lands here unless someone releases it.",
      facts,
      actions: [
        { label: "Explain my settlement", answer: settleWords ? settleWords : "Press Suggest payments, and the clerk posts the fewest payments that would settle it. Nobody's asked yet.", point: settleWords ? undefined : '[data-text="settle"]' },
        ...(d.pot ? [{ label: "How much is still outstanding?", answer: asked ? `${cash(left)} of the ${cash(asked)} asked for.` : "Nobody's been asked for money yet." }] : []),
        { label: "What can the group see?", answer: "Date, amount, category and who shared it, and only for what its owner released. No merchant names, and no statements." },
        { label: "Why can't the group see my statement?", answer: STATEMENT_WALL },
      ],
    };
  }

  /* ---- the trip board: only what's in the group room ---- */
  function board(d, money, square) {
    const released = d.expenses.filter(e => e.status === "released").length;
    const reasons = [];
    const pending = d.transfers.filter(t => ["awaiting_receiver", "challenged"].includes(t.status)).length;
    if (d.held_count) reasons.push(`${d.held_count} ${d.held_count === 1 ? "chit is" : "chits are"} still waiting on their owner's release.`);
    if (pending) reasons.push(`${pending} reported ${pending === 1 ? "payment isn't" : "payments aren't"} confirmed yet.`);
    if (d.plan.length) reasons.push(`${d.plan.length} ${d.plan.length === 1 ? "payment is" : "payments are"} left to make.`);
    const actions = [];
    const facts = [];
    let say = released ? `${released} ${released === 1 ? "expense" : "expenses"} counted so far.` : "Nothing counted yet. Expenses appear once their owners release them.";
    let mood = "idle";
    if (d.pot) {
      const x = potTotals(d.pot);
      facts.push(["Expected", money(x.asked)], ["Confirmed", money(x.credited)], ["Outstanding", money(x.outstanding)]);
      const waitingOn = d.pot.contributions.filter(c => ["submitted", "recipient_review", "needs_clarification"].includes(c.status)).length;
      if (waitingOn) { reasons.unshift(`${waitingOn} ${waitingOn === 1 ? "payment is" : "payments are"} waiting for ${d.pot.treasurer}.`); mood = "waiting"; }
      say = x.asked ? `${money(x.credited)} confirmed of ${money(x.asked)} expected. ${waitingOn ? `${waitingOn} waiting for ${d.pot.treasurer}.` : ""}` : "Nobody's been asked for money yet.";
      const owing = d.pot.roll.filter(r => r.outstanding).map(r => `${r.name}: ${money(r.outstanding)}`);
      actions.push({ label: "How much is still outstanding?", answer: x.asked ? (owing.length ? [`${money(x.outstanding)} in all.`, ...owing] : "Nothing. Everyone who was asked has paid in, and it's been confirmed.") : "Nobody's been asked for money yet." });
      actions.push({ label: "How do payment confirmations work?", answer: CONFIRM });
    }
    actions.push({ label: "Explain the settlement", answer: d.plan.length ? ["The fewest payments that would settle it, from what's confirmed so far:", ...d.plan.map(p => `${p.from} pays ${p.to} ${money(p.paise)}`)] : released ? "No payments are left to make." : "There's nothing to settle yet." });
    actions.push({ label: "Why isn't it square yet?", answer: square ? "It is." : reasons.length ? reasons : "Nothing has been counted yet." });
    actions.push({ label: "What can the group see?", answer: "Only what's in the group room: dates, amounts, categories and who shared. This board never receives merchant names or statements." });
    if (square) { say = "Everyone's square. Every payment was confirmed by whoever received it."; mood = "reconciled"; }
    return { id: "board", title: "The trip board", mood, say, facts, actions };
  }

  window.Kitty = Object.assign(window.Kitty || {}, { guide: { home, newTrip, people, review, building, live, person, board, trail, tripStep, potTotals } });
})();
