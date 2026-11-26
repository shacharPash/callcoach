import Foundation

enum Phase: Equatable { case starting, warming, idle, active, stopping, ended }

enum Presentation: Equatable { case log, note, banner, stickyBanner, agentDot, summary }

let errorPresentation: [String: Presentation] = [
    "bad_command": .log, "command_failed": .log, "capture": .note, "source": .banner, "no_sources": .banner, "engine": .banner,
    "auto_stop": .summary, "recording_stopped": .stickyBanner, "transcript_stopped": .banner,
    "transcription_start_failed": .banner, "transcription_failed": .log, "transcription_stopped": .stickyBanner,
    "langid_failed": .log, "langid_stopped": .note, "system_audio_dropout": .log,
    "agent_unavailable": .agentDot, "agent_auth": .agentDot, "summary_failed": .summary, "no_agent_account": .banner,
    "export_failed": .summary,
]

var errorTitles: [String: String] {
    [
        "capture": L("השמע מתחיל מחדש", "Audio is restarting"),
        "source": L("מקור שמע לא עלה", "An audio source did not start"),
        "no_sources": L("אין מקור שמע, השיחה הסתיימה", "No audio source, the call ended"),
        "engine": L("המנוע נכשל, השיחה הסתיימה", "The engine failed, the call ended"),
        "auto_stop": L("אין דיבור כבר 5 דקות, השיחה הסתיימה", "No speech for 5 minutes, the call ended"),
        "recording_stopped": L("ההקלטה נעצרה (אולי הדיסק מלא). התמלול ממשיך",
                               "Recording stopped (the disk may be full). Transcription continues"),
        "transcript_stopped": L("קובץ התמלול נעצר. השורות עדיין מוצגות", "The transcript file stopped. Lines are still shown"),
        "transcription_start_failed": L("התמלול לא עלה. השיחה רק מוקלטת",
                                        "Transcription did not start. The call is only recorded"),
        "transcription_stopped": L("התמלול נעצר. ההקלטה ממשיכה", "Transcription stopped. Recording continues"),
        "langid_stopped": L("זיהוי השפה נעצר", "Language detection stopped"),
        "system_audio_dropout": L("השמע של הצד השני נקטע לרגע", "The other side's audio cut out for a moment"),
        "agent_unavailable": L("הסוכן לא זמין כרגע", "The agent is not available right now"),
        "agent_auth": L("החשבון נדחה, צריך להתחבר מחדש", "The account was rejected, sign in again"),
        "no_agent_account": L("אין חשבון פעיל למאמן", "No working account for the coach"),
        "summary_failed": L("הסיכום המלא נכשל, נכתב סיכום מקומי", "The full summary failed, a local summary was written"),
        "export_failed": L("ההעתקה לתיקייה שנבחרה נכשלה", "The copy to the chosen folder failed"),
    ]
}

/// A model term's few sentences, asked for when its row is first opened.
enum TermDetailState: Equatable {
    case asking, ready(String), failed
}

/// "עוד מהמודל" on a known name whose file has its detail: asked, added under the file's detail, or failed, which
/// leaves the file's detail and lets it be asked again.
enum TermMoreState: Equatable {
    case asking, added, failed
}

/// "מה המושג שנאמר עכשיו?": searching until the engine answers, then a short note when it found nothing or
/// failed. A found term is pointed at as the fresh one instead.
enum SpotState: Equatable {
    case searching, nothing, failed
}

/// "הוסף לידע" on an open row: added to the calls' knowledge file, already in a knowledge file, or the write failed.
enum KnowledgeAdd: Equatable {
    case added, known, failed
}

/// "סיכום ביניים": nothing to sum up before a word was said, then up to five short lines of the whole call.
enum RecapState: Equatable {
    case empty, asking, ready([String]), failed

    static let most = 5

    /// The lines of an answer, each without a bullet mark; an empty or failed answer is a failure.
    static func from(_ recap: Recap) -> RecapState {
        let lines = Self.lines(recap.text)
        return recap.ok && !lines.isEmpty ? .ready(Array(lines.prefix(most))) : .failed
    }

    static func lines(_ text: String) -> [String] {
        text.components(separatedBy: "\n").map { line -> String in
            var t = line.trimmingCharacters(in: .whitespaces)
            for mark in ["- ", "• ", "* "] where t.hasPrefix(mark) { t = String(t.dropFirst(mark.count)) }
            return t.trimmingCharacters(in: .whitespaces)
        }.filter { !$0.isEmpty }
    }
}

/// What a refresh added under the recap: the lines on what was said since a time of the call.
struct RecapPart: Equatable {
    static let most = 3
    /// The box shows the newest parts only, so it never grows past the panel.
    static let shown = 4
    let since: String
    let lines: [String]
}

/// The refresh arrow of the recap: asked, nothing new was said, or it failed and what was there stays.
enum RecapUpdate: Equatable { case asking, nothingNew, failed }

/// A feature of the call, switched on or off at any moment. Every chip but the transcript asks the model, so the
/// call is in מאמן while any of those is on and in הקלטה when none is.
enum Chip: String, CaseIterable {
    case transcript, translate, recap, ask, terms

    static let ai: Set<Chip> = [.translate, .recap, .ask, .terms]

    var title: String {
        switch self {
        case .transcript: return L("כתוביות", "Captions")
        case .translate: return L("תרגום", "Translation")
        case .recap: return L("סיכום ביניים", "Recap")
        case .ask: return L("שאלות", "Questions")
        case .terms: return L("מושגים", "Terms")
        }
    }

    var symbol: String {
        switch self {
        case .transcript: return "text.quote"
        case .translate: return "character.bubble"
        case .recap: return "list.bullet.rectangle"
        case .ask: return "questionmark.bubble"
        case .terms: return "text.book.closed"
        }
    }
}

/// The chips that are on. The engine computes "שאלות" and "מושגים" only while their chip is on.
struct Chips: Equatable {
    var on: Set<Chip>

    subscript(_ chip: Chip) -> Bool { on.contains(chip) }
    var coach: Bool { !on.isDisjoint(with: Chip.ai) }
    var sections: Sections { Sections(ask: on.contains(.ask), terms: on.contains(.terms)) }
}

/// The sections the engine computes. `now` is the line on what is discussed now, at the top of "סיכום ביניים": on
/// only while that chip is on and its switch is, so it costs nothing otherwise.
struct Sections: Equatable {
    var ask = true
    var terms = true
    var now = false
    /// "רק מהרשימה": the terms come from the knowledge files and the brief only, and the live model is not asked for
    /// them; sent on only while "מושגים" is on.
    var listOnly = false
}

/// A banner or a note. `seconds` is how long it shows once the call is active; nil keeps it until replaced.
struct Notice: Equatable {
    let title: String
    let detail: String
    let seconds: Double?
    var until: Double?
    /// A "הגדרות" button after the title that opens the accounts page.
    var accounts = false
}

/// A click on a switch: its value, and until when a status that does not have it yet is not taken.
struct Click: Equatable {
    let on: Bool
    let until: Double
}

struct CallModel: Equatable {
    static let noteSeconds = 5.0
    static let bannerSeconds = 30.0
    static let freshTermSeconds = 10.0
    static let spotNoteSeconds = 4.0
    /// The line of what the screen shows is up to 25 words, so it stays longer than a note.
    static let screenNoteSeconds = 12.0
    static let recentLineCount = 2
    /// With no AI chip on, the transcript is all the panel shows, so it keeps more lines.
    static let recordLineCount = 6
    static let partialSeconds = 8.0
    /// A line whose start is this close to a partial's start is the line of the same sentence.
    static let partialMatch = 0.1
    static let stopConfirmSeconds: Double = 5
    /// "שאלה עכשיו" says it is looking until the question comes, or for this long.
    static let askSeconds = 12.0
    /// A click on the hearing switch or on pause is shown at once; a status that still has the old value is not, for
    /// this long.
    static let hearingSettleSeconds = 3.0

    var phase = Phase.starting
    var status: Status?
    /// Whether CallCoach hears me. Every call starts hearing; a click turns it off and on.
    var hearing = true
    /// The value of the last click and until when a status that does not have it yet is not taken.
    var hearingClick: Click?
    /// Whether the recording is paused: neither side is heard. Every call starts recording; a click pauses it and
    /// goes on.
    var paused = false
    var pausedClick: Click?
    /// The features on in the panel; a section shows only while its chip is on.
    var chips = Chips(on: [.ask, .terms])
    /// Whether the coach was on at any point of the call: the stop's default and the chips remembered after it.
    var coachEver = false
    /// The value of `coachEver` before the last switch that turned the coach on, for a coach the engine refused.
    var coachEverBefore = false
    /// Whether the stop that was sent asks for a summary; the finishing line says which.
    var stopSummary: Bool?
    /// The AI chips that were on the last time the coach was on in this call, for "מאמן" to bring back.
    var lastCoachChips: Set<Chip> = []
    var agent = "off"
    var visible = true
    var lastLine: TranscriptLine?
    /// The latest lines, oldest first, for the two transcript rows of the panel.
    var recentLines: [TranscriptLine] = []
    /// The Hebrew of the other side's English lines, by line id, for the lines the panel still shows.
    var translations: [Int: String] = [:]
    /// The call started as a recording (the start's `record_only`); the chips say whether the coach is on now.
    var recordOnly = false
    /// The call app the watch started this call for ("הקלט שיחות אוטומטית"); nil for a call started by hand. Such a
    /// call starts with the panel hidden and stops when that app lets go of the mic and the sound.
    var autoApp: String?
    /// The other side's open sentence, until its line comes or 8 s pass with no update.
    var partial: Partial?
    var partialUntil = 0.0
    /// The start of each side's newest line, so a partial that comes after its line is not shown.
    var lineStarts: [String: Double] = [:]
    var goals: [Goal] = []
    /// The goal "שאל על זה" put in focus; it ends when the goal is reached or the button is pressed again.
    var focusGoal: String?
    var cards = CardQueue()
    /// In the order they came, so a row never moves. The engine sends each term once; an id seen again is ignored.
    var terms: [Term] = []
    /// The rows I marked "לא להקפיץ" in this call; the engine leaves them out from the next call.
    var hiddenTerms: Set<Int> = []
    /// Live questions are part of this call once the engine sends its first questions event.
    var liveQuestions = false
    var questions: [Question] = []
    /// "שאלה עכשיו" was pressed and its question did not come yet; until then the button says it is looking.
    var askingUntil: Double?
    /// "הם שאלו אותך": a question asked of me that I did not answer yet.
    var asked: Asked?
    /// The switch on the title of "סיכום ביניים" for the line on what is discussed now; remembered across calls.
    var nowLineOn = true
    /// That line, from the engine's `topic`; nil before the first one and after the line was turned off.
    var nowLine: String?
    /// "רק מהרשימה" on the title of "מושגים"; remembered across calls, off by default.
    var listOnly = false
    /// What the engine answered for "סיכום ביניים".
    var recap: RecapState?
    var recapParts: [RecapPart] = []
    var recapUpdate: RecapUpdate?
    /// The ids of the lines the call had, so one that arrives again counts once.
    private var lineIds: Set<Int> = []
    /// How many lines were said in the call; the recap has nothing to sum up while it is zero.
    var lineCount: Int { lineIds.count }
    var freshTermId: Int?
    var freshTermUntil = 0.0
    var spot: SpotState?
    var spotUntil = 0.0
    /// The words of the last lines to pick from, open beside the search.
    var picker: TermPicker?
    /// Picked words were sent since the last search began, so the search's own answer is left out.
    var pickSent = false
    /// The row open in the panel; one at a time.
    var openTermId: Int?
    /// What the engine said about each model term that was opened, so opening it again shows it at once.
    var termDetails: [Int: TermDetailState] = [:]
    /// "עוד מהמודל" on each known name it was pressed on.
    var termMore: [Int: TermMoreState] = [:]
    /// The rows added to the knowledge in this call, so the row says so.
    var knowledgeAdds: [Int: KnowledgeAdd] = [:]
    /// The goals list is folded into one line until it is clicked.
    var goalsOpen = false
    /// The list "…" opens in the panel.
    var menuOpen = false
    /// The "?" window under the chips: what the call uses now.
    var usageOpen = false
    /// The panel shows only its status line until it is unfolded.
    var folded = false
    /// Stop was clicked once; a second click before this time stops the call.
    var stopConfirmUntil: Double?
    var account: AccountState?
    var usage: UsageEvent?
    var folder: String?
    var finalized: Finalized?
    var summary: SummaryReady?
    var summaryNotes: [String] = []
    var banner: Notice?
    var note: Notice?
    var fatal: String?

    /// The sections the engine computes: those of the chips, and the now line while "סיכום ביניים" and its switch are on.
    var sections: Sections { Self.sections(chips, nowLine: nowLineOn, listOnly: listOnly) }

    static func sections(_ chips: Chips, nowLine: Bool, listOnly: Bool = false) -> Sections {
        var s = chips.sections
        s.now = chips[.recap] && nowLine
        s.listOnly = chips[.terms] && listOnly
        return s
    }

    /// "רק מהרשימה": the engine hears it at once while "מושגים" is on. The terms already shown stay.
    mutating func toggleListOnly() -> [Command] {
        let before = sections
        listOnly.toggle()
        guard phase == .active, sections != before else { return [] }
        return [.setSections(sections)]
    }

    /// The switch beside the title of "סיכום ביניים": the engine hears it at once while the recap is on; off forgets
    /// the line, so on again it waits for the next one.
    mutating func toggleNowLine() -> [Command] {
        let before = sections
        nowLineOn.toggle()
        if !nowLineOn { nowLine = nil }
        guard phase == .active, sections != before else { return [] }
        return [.setSections(sections)]
    }

    /// The terms the panel shows: all but the ones I marked as known.
    var shownTerms: [Term] {
        terms.filter { !hiddenTerms.contains($0.id) }
    }

    var goalProgress: String {
        L("\(goals.filter(\.done).count) מתוך \(goals.count)", "\(goals.filter(\.done).count) of \(goals.count)")
    }

    /// The folded goals line: "מטרות 1/3".
    var goalCount: String {
        L("מטרות \(goals.filter(\.done).count)/\(goals.count)", "Goals \(goals.filter(\.done).count)/\(goals.count)")
    }

    /// A click on a goal's check: checked or unchecked by hand, and the coach leaves it so.
    mutating func toggleGoal(_ id: String) -> [Command] {
        guard phase == .active, let index = goals.firstIndex(where: { $0.id == id }) else { return [] }
        goals[index].done.toggle()
        if goals[index].done && focusGoal == id { focusGoal = nil }
        return [.setGoal(id, goals[index].done)]
    }

    /// "שאל על זה" on an open goal: the coach asks toward it at once and keeps it as the focus; on the goal in
    /// focus it ends the focus. Questions turn on first when they are off, and the panel unfolds for them.
    mutating func focus(_ id: String) -> [Command] {
        guard phase == .active, goals.contains(where: { $0.id == id && !$0.done }) else { return [] }
        if focusGoal == id {
            focusGoal = nil
            return [.focusGoal(nil)]
        }
        focusGoal = id
        return (chips[.ask] ? [] : turnOn(.ask)) + [.focusGoal(id)]
    }

    /// The ear lights while CallCoach hears my voice; never while it does not hear me.
    var meVoice: Bool {
        hearing && !paused && status?.meVoice == true
    }

    /// "הם שאלו אותך" shows only while CallCoach hears me, the recording is not paused and "שאלות" is on.
    var shownAsked: Asked? {
        hearing && !paused && chips[.ask] ? asked : nil
    }

    mutating func apply(_ event: Event, now: Double) -> [Command] {
        switch event {
        case .hello(let h) where h.protocol != protocolVersion:
            fatal = L("גרסת המנוע (\(h.protocol)) לא מתאימה לאפליקציה (\(protocolVersion)). צריך להריץ שוב את setup.sh.",
                      "The engine version (\(h.protocol)) does not match the app (\(protocolVersion)). Run setup.sh again.")
        case .status(let s):
            status = s
            agent = s.agent
            takeHearing(s.hearing, now: now)
            takePaused(s.paused, now: now)
            if phase != .ended { phase = Self.phase(s.state) ?? phase }
        case .ready(let r):
            folder = r.folder
            phase = .active
        case .line(let l):
            lineIds.insert(l.id)
            if lastLine.map({ l.tStart >= $0.tStart }) ?? true { lastLine = l }
            recentLines = Array((recentLines + [l]).sorted { $0.tStart < $1.tStart }
                .suffix(chips.coach ? Self.recentLineCount : Self.recordLineCount))
            translations = translations.filter { id, _ in recentLines.contains { $0.id == id } }
            lineStarts[l.channel] = max(lineStarts[l.channel] ?? -.infinity, l.tStart)
            if let p = partial, p.channel == l.channel, l.tStart >= p.tStart - Self.partialMatch { partial = nil }
        case .partial(let p):
            guard !p.text.isEmpty, p.tStart > (lineStarts[p.channel] ?? -.infinity) + Self.partialMatch else { break }
            partial = p
            partialUntil = now + Self.partialSeconds
        case .card(let c):
            if c.forced { askingUntil = nil }
            guard chips[.ask] else { break }
            return cards.add(c, now: now, visible: visible).map { [.shown(cardId: $0.id, t: now)] } ?? []
        case .term(let t):
            guard !terms.contains(where: { $0.id == t.id }) else { break }
            terms.append(t)
            freshTermId = t.id
            freshTermUntil = now + Self.freshTermSeconds
        case .termDetail(let d):
            let ok = d.ok && !d.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            // The answer to "עוד מהמודל" replaces the file's detail only when it came, since it holds it; a failure
            // keeps the file's detail on screen.
            if termMore[d.id] == .asking {
                termMore[d.id] = ok ? .added : .failed
                if ok { termDetails[d.id] = .ready(d.text) }
            } else {
                termDetails[d.id] = ok ? .ready(d.text) : .failed
            }
        case .questions(let q):
            guard chips[.ask] else { break }
            liveQuestions = true
            let items = q.items.filter { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            if !items.isEmpty || questions.isEmpty { questions = items }
        case .asked(let a):
            let text = a.text.trimmingCharacters(in: .whitespacesAndNewlines)
            asked = text.isEmpty || !hearing || paused || !chips[.ask] ? nil : a
        case .termsNow(let found):
            if pickSent && !found.picked { break }
            if found.ok, let id = found.ids.first(where: { id in shownTerms.contains { $0.id == id } }) {
                spot = nil
                freshTermId = id
                freshTermUntil = now + Self.freshTermSeconds
            } else {
                spot = found.ok ? SpotState.nothing : .failed
                spotUntil = now + Self.spotNoteSeconds
            }
        case .translation(let t):
            if recentLines.contains(where: { $0.id == t.id }) { translations[t.id] = t.he }
        case .screen(let s):
            note = s.ok ? notice(L("מהמסך", "From the screen"), s.text, Self.screenNoteSeconds, now)
                : notice(L("לא הצלחתי לקרוא את המסך", "Could not read the screen"), "", Self.noteSeconds, now)
        case .topic(let t):
            guard sections.now else { break }
            let text = t.text.trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty { nowLine = text }
        case .recap(let r):
            if let since = r.since {
                let lines = RecapState.lines(r.text)
                if !r.ok {
                    recapUpdate = .failed
                } else if lines.isEmpty {
                    recapUpdate = .nothingNew
                } else {
                    recapParts.append(RecapPart(since: since, lines: Array(lines.prefix(RecapPart.most))))
                    recapUpdate = nil
                }
                break
            }
            let next = RecapState.from(r)
            if case .ready = recap, next == .failed { break }
            recap = next
        case .goals(let g):
            goals = g
            if let f = focusGoal, goals.first(where: { $0.id == f })?.done != false { focusGoal = nil }
        case .finalized(let f): finalized = f
        case .summaryReady(let s): summary = s
        case .usage(let u): usage = u
        case .account(let a): account = a
        case .error(let e): present(e, now: now)
        case .stopped: phase = .ended
        case .hello, .segment, .unknown: break
        }
        return []
    }

    /// "אני מכיר, לא להקפיץ": the row goes from the panel at once. A note says where to bring it back, or that it
    /// was not saved, so only this call hides it.
    mutating func hideTerm(_ id: Int, saved: Bool, now: Double) {
        guard let term = terms.first(where: { $0.id == id }) else { return }
        hiddenTerms.insert(id)
        if openTermId == id { openTermId = nil }
        if freshTermId == id { freshTermId = nil }
        note = saved
            ? notice(L("לא אקפיץ יותר את \(term.term)", "\(term.term) won't show again"),
                     L("אפשר להחזיר בהגדרות, בלשונית לא להקפיץ", "Bring it back in Settings, under Don't show"), Self.noteSeconds, now)
            : notice(L("לא הצלחתי לשמור ברשימה", "Could not save it to the list"),
                     L("\(term.term) מוסתר רק בשיחה הזאת", "\(term.term) is hidden in this call only"), Self.noteSeconds, now)
    }

    /// What came of "העתק את התמלול" in the panel.
    mutating func transcriptCopied(_ count: Int, now: Double) {
        note = count > 0
            ? notice(L("התמלול הועתק", "The transcript was copied"),
                     count == 1 ? L("שורה אחת", "1 line") : L("\(count) שורות", "\(count) lines"), Self.noteSeconds, now)
            : notice(L("אין עדיין תמלול להעתיק", "No transcript to copy yet"), "", Self.noteSeconds, now)
    }

    /// "שאלה עכשיו": turns "שאלות" on when it is off (in a recording that turns the coach on with it), unfolding the
    /// panel so the answer can be read, and asks the engine for a question at once.
    mutating func askNow(now: Double) -> [Command] {
        guard phase == .active else { return [] }
        let open = chips[.ask] ? [] : turnOn(.ask)
        askingUntil = now + Self.askSeconds
        return open + [.action(Actions.ask.id)]
    }

    /// Opens a row, or closes it when it is open. A model term asks the engine for its few sentences the first time
    /// it opens; one that failed asks again when it opens again. A known name with a detail in its file asks too,
    /// and the engine answers from the file at once; after the call it shows what it has.
    mutating func toggleTerm(_ id: Int) -> [Command] {
        guard let term = terms.first(where: { $0.id == id }) else { return [] }
        if openTermId == id {
            openTermId = nil
            return []
        }
        openTermId = id
        let fromFile = term.known == true && term.detail == true
        guard term.known != true || fromFile, termDetails[id] == nil || termDetails[id] == .failed else { return [] }
        if fromFile && phase != .active { return [] }
        guard phase == .active else {
            termDetails[id] = .failed
            return []
        }
        termDetails[id] = .asking
        return [.explainTerm(id)]
    }

    /// Whether "עוד מהמודל" shows on a known name: its file's detail is shown, the model is on, and its answer was
    /// not added yet.
    func canAskMore(_ id: Int) -> Bool {
        guard phase == .active, chips.coach, let term = terms.first(where: { $0.id == id }), term.known == true,
              term.detail == true, case .ready? = termDetails[id] else { return false }
        return termMore[id] == nil || termMore[id] == .failed
    }

    /// "עוד מהמודל": asks the model about a known name all the same; the answer comes added under the file's detail.
    mutating func askMore(_ id: Int) -> [Command] {
        guard canAskMore(id) else { return [] }
        termMore[id] = .asking
        return [.explainMore(id)]
    }

    /// "מה המושג שנאמר עכשיו?": turns "מושגים" on when it is off (in a recording that turns the coach on with it),
    /// unfolding the panel so the answer can be read, asks the engine to look at once at what was just said, and opens
    /// the last lines as words to pick; a press while it looks does nothing.
    mutating func termsNow(now: Double) -> [Command] {
        guard phase == .active, spot != .searching else { return [] }
        let open = chips[.terms] ? [] : turnOn(.terms)
        spot = .searching
        pickSent = false
        picker = TermPicker(lines: recentLines.map(\.text) + [partial?.text].compactMap { $0 }, now: now)
        return open + [.action("terms_now")]
    }

    mutating func togglePick(_ spot: TermPicker.Spot, now: Double) {
        picker?.toggle(spot, now: now)
    }

    /// "בדוק": the picked words go to the engine and the picker closes; the answer points at what they name.
    mutating func checkPicked() -> [Command] {
        guard phase == .active, chips[.terms], let phrases = picker?.phrases, !phrases.isEmpty else { return [] }
        picker = nil
        spot = .searching
        pickSent = true
        return [.checkTerms(phrases)]
    }

    mutating func closePicker() {
        picker = nil
    }

    /// A screenshot of the call's window was taken and sent; the engine's line on it comes as a note.
    mutating func screenSent(now: Double) {
        note = notice(L("צילמתי את חלון השיחה", "Took the call window"), L("קורא אותו…", "Reading it…"),
                      Self.screenNoteSeconds, now)
    }

    /// Why no screenshot was taken.
    mutating func screenFailed(_ why: String, now: Double) {
        note = notice(L("לא צילמתי את המסך", "No screenshot taken"), why, Self.screenNoteSeconds, now)
    }

    /// A click on the hearing switch shows at once and tells the engine. Not hearing me drops "הם שאלו אותך".
    mutating func toggleHearing(now: Double) -> [Command] {
        guard phase == .active else { return [] }
        hearing.toggle()
        hearingClick = Click(on: hearing, until: now + Self.hearingSettleSeconds)
        if !hearing { asked = nil }
        return [.setHearing(hearing)]
    }

    /// The engine's value, unless it is a status from before the last click.
    private mutating func takeHearing(_ value: Bool?, now: Double) {
        let (on, click) = Self.settled(value, hearing, hearingClick, now: now)
        if hearing && !on { asked = nil }
        (hearing, hearingClick) = (on, click)
    }

    /// A click on pause shows at once and tells the engine. Pausing drops "הם שאלו אותך" and the other side's open
    /// sentence, since nothing is heard until it goes on.
    mutating func togglePause(now: Double) -> [Command] {
        guard phase == .active else { return [] }
        paused.toggle()
        pausedClick = Click(on: paused, until: now + Self.hearingSettleSeconds)
        if paused { dropHeard() }
        return [.setPaused(paused)]
    }

    private mutating func takePaused(_ value: Bool?, now: Double) {
        let (on, click) = Self.settled(value, paused, pausedClick, now: now)
        if !paused && on { dropHeard() }
        (paused, pausedClick) = (on, click)
    }

    private mutating func dropHeard() {
        asked = nil
        partial = nil
    }

    /// A switch's value from a status: the engine's, unless the status is from before the last click. A status that
    /// agrees ends the wait, and so do 3 s; an older engine sends none and the value stays.
    static func settled(_ value: Bool?, _ current: Bool, _ click: Click?, now: Double) -> (Bool, Click?) {
        guard let value else { return (current, click) }
        if let click, value != click.on, now < click.until { return (current, click) }
        return (value, nil)
    }

    /// The chips a call starts with. A call with the coach takes the AI chips of the last call that was in מאמן,
    /// else questions and terms; a recording starts with none. The transcript is remembered across all calls, and
    /// translation always brings it.
    static func startChips(recording: Bool, transcript: Bool, remembered: Set<Chip>?) -> Chips {
        var on: Set<Chip> = transcript ? [.transcript] : []
        if !recording {
            let ai = (remembered ?? []).intersection(Chip.ai)
            on.formUnion(ai.isEmpty ? [.ask, .terms] : ai)
        }
        if on.contains(.translate) { on.insert(.transcript) }
        return Chips(on: on)
    }

    /// A click on a chip. An AI chip in a recording turns the coach on with that chip alone; translation turns the
    /// transcript on, and the transcript off takes translation with it.
    mutating func toggleChip(_ chip: Chip) -> [Command] {
        guard phase == .active else { return [] }
        var next = chips.on
        if next.contains(chip) {
            next.remove(chip)
            if chip == .transcript { next.remove(.translate) }
        } else {
            next.insert(chip)
            if chip == .translate { next.insert(.transcript) }
        }
        return setChips(Chips(on: next))
    }

    /// A chip a key turned on for its answer; the panel unfolds, since a folded one shows no section.
    private mutating func turnOn(_ chip: Chip) -> [Command] {
        folded = false
        return toggleChip(chip)
    }

    /// "מאמן": off turns every AI chip off; on brings back the AI chips of the last time in this call, else of the
    /// last call with the coach, else questions and terms.
    mutating func toggleCoach(remembered: Set<Chip>? = nil) -> [Command] {
        guard phase == .active else { return [] }
        if chips.coach { return setChips(Chips(on: chips.on.subtracting(Chip.ai))) }
        let back = lastCoachChips.isEmpty ? (remembered ?? []).intersection(Chip.ai) : lastCoachChips
        var on = chips.on.union(back.isEmpty ? [.ask, .terms] : back)
        if on.contains(.translate) { on.insert(.transcript) }
        return setChips(Chips(on: on))
    }

    /// Moves to new chips and says what the engine must hear: the coach first when it turns on, last when it turns
    /// off, and the sections when they change. "שאלות" off forgets what it showed, so it is empty until the next request
    /// fills it; "מושגים" off keeps the call's terms; "סיכום ביניים" off keeps what it had. The coach on keeps only the lines its transcript shows. Translation
    /// follows in AppState, since it also depends on the panel.
    private mutating func setChips(_ next: Chips) -> [Command] {
        let before = chips
        let beforeSections = sections
        chips = next
        if next.coach && !before.coach {
            recentLines = Array(recentLines.suffix(Self.recentLineCount))
            translations = translations.filter { id, _ in recentLines.contains { $0.id == id } }
        }
        if before[.ask] && !next[.ask] {
            askingUntil = nil
            questions = []
            liveQuestions = false
            asked = nil
            cards.clear()
        }
        if before[.terms] && !next[.terms] { picker = nil }
        if !sections.now { nowLine = nil }
        if before.coach { lastCoachChips = before.on.intersection(Chip.ai) }
        if next.coach { lastCoachChips = next.on.intersection(Chip.ai) }
        var commands: [Command] = []
        if next.coach && !before.coach {
            coachEverBefore = coachEver
            coachEver = true
            commands.append(.setCoach(true))
        }
        if sections != beforeSections || (next.coach && !before.coach) { commands.append(.setSections(sections)) }
        if !next.coach && before.coach { commands.append(.setCoach(false)) }
        return commands
    }

    /// "עדכן": what was said since the last answer, added under what the recap has. One at a time.
    mutating func updateRecap() -> [Command] {
        guard phase == .active, case .ready = recap, recapUpdate != .asking else { return [] }
        recapUpdate = .asking
        return [.recapSince]
    }

    /// What סיכום ביניים shows when it comes into view (its chip turns on, the panel unfolds or is shown again) or
    /// its round arrow is pressed: the whole call the first time, then only what is new since. Nothing is asked
    /// before anything was said, for a panel that cannot be read, or while a request runs.
    mutating func refreshRecap() -> [Command] {
        guard phase == .active, chips[.recap], visible, !folded else { return [] }
        guard lineCount > 0 else {
            recap = .empty
            return []
        }
        if case .ready = recap { return updateRecap() }
        if recap == .asking { return [] }
        recap = .asking
        return [.recap]
    }

    /// A click on "לא הצלחתי לסכם" tries again.
    mutating func askRecap() -> [Command] {
        guard phase == .active else {
            recap = .failed
            return []
        }
        recap = .asking
        return [.recap]
    }

    /// The first click on עצור opens its second step for a few seconds; nothing stops yet.
    mutating func pressStop(now: Double) -> [Command] {
        guard phase == .active, stopSummary == nil else { return [] }
        stopConfirmUntil = now + Self.stopConfirmSeconds
        return []
    }

    /// One of the second step's buttons: stops the call, with or without the summary.
    mutating func confirmStop(summary: Bool) -> [Command] {
        guard phase == .active, stopSummary == nil, stopConfirmUntil != nil else { return [] }
        stopConfirmUntil = nil
        return [.stop(summary: summary)]
    }

    static func phase(_ state: String) -> Phase? {
        ["warming": .warming, "idle": .idle, "active": .active, "stopping": .stopping][state]
    }

    mutating func present(_ e: EngineError, now: Double) {
        let title = errorTitles[e.code] ?? L("שגיאה", "Error")
        if e.code == "no_agent_account" {
            chips = Chips(on: chips.on.subtracting(Chip.ai))
            coachEver = coachEverBefore
            banner = notice(title, "", Self.bannerSeconds, now)
            banner?.accounts = true
            return
        }
        switch errorPresentation[e.code] ?? .banner {
        case .log: break
        case .note: note = notice(title, e.message, Self.noteSeconds, now)
        case .banner: banner = notice(title, e.message, Self.bannerSeconds, now)
        case .stickyBanner: banner = Notice(title: title, detail: e.message, seconds: nil, until: nil)
        case .agentDot: agent = e.code == "agent_auth" ? "auth" : "unavailable"
        case .summary: summaryNotes.append(title)
        }
    }

    /// A note I added reached the coach; the panel says so for a few seconds.
    mutating func noted(now: Double) {
        let follows = liveQuestions && chips[.ask]
        note = notice(L("המאמן עודכן", "The coach got it"),
                      follows ? L("השאלות יתחלפו בהתאם", "The questions will follow it") : "", Self.noteSeconds, now)
    }

    /// "לא בכיוון" on a live question takes it off the panel at once; false when it is not shown.
    mutating func dropQuestion(_ question: Question) -> Bool {
        guard phase == .active, let index = questions.firstIndex(of: question) else { return false }
        questions.remove(at: index)
        return true
    }

    /// "לא בכיוון" on a question in the slot (one I asked for) takes it off at once; false when it is not shown.
    mutating func dropCard(_ card: Card) -> Bool {
        guard phase == .active, card.kind == "question" else { return false }
        return cards.drop(card)
    }

    mutating func toggleFold() {
        folded.toggle()
        if folded { menuOpen = false; usageOpen = false }
    }

    private func notice(_ title: String, _ detail: String, _ seconds: Double, _ now: Double) -> Notice {
        Notice(title: title, detail: detail, seconds: seconds, until: phase == .active ? now + seconds : nil)
    }

    mutating func tick(now: Double) -> [Command] {
        if phase == .active {
            if let seconds = note?.seconds, note?.until == nil { note?.until = now + seconds }
            if let seconds = banner?.seconds, banner?.until == nil { banner?.until = now + seconds }
        }
        if let until = note?.until, now >= until { note = nil }
        if let until = banner?.until, now >= until { banner = nil }
        if freshTermId != nil, now >= freshTermUntil { freshTermId = nil }
        if spot == SpotState.nothing || spot == .failed, now >= spotUntil { spot = nil }
        if partial != nil, now >= partialUntil { partial = nil }
        if let p = picker, p.closes(now: now) || phase != .active { picker = nil }
        if let until = stopConfirmUntil, now >= until { stopConfirmUntil = nil }
        if let until = askingUntil, now >= until || phase != .active { askingUntil = nil }
        if let click = hearingClick, now >= click.until { hearingClick = nil }
        if let click = pausedClick, now >= click.until { pausedClick = nil }
        return cards.tick(now: now, visible: visible).map { [.shown(cardId: $0.id, t: now)] } ?? []
    }

    /// Shown or hidden; a card that waited while hidden can show at once. The engine hears it from AppState, which
    /// counts a folded panel as hidden too.
    mutating func setVisible(_ value: Bool, now: Double) -> [Command] {
        guard value != visible else { return [] }
        visible = value
        return tick(now: now)
    }
}

extension SelfCheck {
    static func card(_ id: Int, forced: Bool = false, kind: String = "question", he: String? = nil) -> Card {
        Card(id: id, kind: kind, text: "q\(id)", lang: "he", triggerLineIds: [], requestedAt: 0, receivedAt: 0,
             forced: forced, he: he)
    }

    static func modelChecks(_ root: URL) -> [String] {
        var p: [String] = []
        var q = CardQueue()
        expect(q.add(card(1), now: 0, visible: true)?.id == 1, "the first card shows at once", &p)
        expect(q.add(card(2), now: 3, visible: true) == nil, "a card within 8 s waits", &p)
        expect(q.add(card(3), now: 5, visible: true) == nil && q.waiting?.id == 3, "a newer card replaces the waiting one", &p)
        expect(q.tick(now: 7.9, visible: true) == nil, "nothing before 8 s", &p)
        expect(q.tick(now: 8, visible: true)?.id == 3, "the waiting card shows at 8 s", &p)
        expect(q.add(card(4, forced: true), now: 9, visible: true)?.id == 4, "a forced card shows at once", &p)
        expect(q.add(card(5), now: 30, visible: false) == nil && q.tick(now: 31, visible: false) == nil,
               "nothing shows while hidden", &p)
        expect(q.tick(now: 32, visible: true)?.id == 5, "the waiting card shows when visible again", &p)
        var r = CardQueue()
        _ = r.add(card(1), now: 0, visible: true)
        _ = r.add(card(2), now: 3, visible: true)
        expect(r.add(card(3), now: 9, visible: true)?.id == 3 && r.waiting == nil, "a newer card drops the waiting one", &p)
        expect(r.tick(now: 20, visible: true) == nil, "the dropped card does not come back", &p)
        expect(r.tick(now: 28.9, visible: true) == nil && r.current?.id == 3, "a nudge stays 20 s", &p)
        expect(r.tick(now: 29, visible: true) == nil && r.current == nil, "a nudge leaves after 20 s", &p)
        _ = r.add(card(7, forced: true), now: 30, visible: true)
        _ = r.tick(now: 69.9, visible: true)
        expect(r.current?.id == 7, "an answer to ask_now stays 40 s", &p)
        _ = r.tick(now: 70, visible: true)
        expect(r.current == nil, "an answer to ask_now leaves after 40 s", &p)

        var termsModel = CallModel()
        let soc = Term(id: 1, term: "SOC 2", note: "תקן ביקורת אבטחה", lang: "he", triggerLineIds: [])
        let okta = Term(id: 2, term: "Okta", note: "sign-in for companies", kind: "product", lang: "en", triggerLineIds: [])
        _ = termsModel.apply(.term(soc), now: 0)
        _ = termsModel.apply(.term(okta), now: 2)
        _ = termsModel.apply(.term(soc), now: 3)
        expect(termsModel.terms.map(\.id) == [1, 2], "terms in the order they came, each once", &p)
        expect(termSymbol(okta) == "shippingbox" && termSymbol(soc) == "text.book.closed", "term kind symbols", &p)
        expect(termsModel.freshTermId == 2, "the newest term is fresh", &p)
        _ = termsModel.tick(now: 11.9)
        expect(termsModel.freshTermId == 2, "a term stays fresh 10 s", &p)
        _ = termsModel.tick(now: 12)
        expect(termsModel.freshTermId == nil, "then it is not fresh", &p)
        var spotting = CallModel()
        _ = spotting.apply(.ready(Ready(folder: "/tmp/x", replayT0: nil)), now: 0)
        _ = spotting.apply(.term(soc), now: 0)
        _ = spotting.toggleChip(.terms)
        expect(spotting.termsNow(now: 0) == [.setSections(Sections()), .action("terms_now")]
               && spotting.spot == .searching && spotting.chips[.terms], "the search turns מושגים on and asks at once", &p)
        expect(spotting.termsNow(now: 0).isEmpty, "a press while it looks does nothing", &p)
        _ = spotting.apply(.term(okta), now: 20)
        _ = spotting.apply(.termsNow(TermsNow(ok: true, ids: [1, 2])), now: 20)
        expect(spotting.spot == nil && spotting.freshTermId == 1 && spotting.freshTermUntil == 30,
               "the term said last is pointed at, a shown one too", &p)
        expect(spotting.termsNow(now: 0) == [.action("terms_now")], "asked again", &p)
        _ = spotting.apply(.termsNow(TermsNow(ok: true, ids: [])), now: 40)
        expect(spotting.spot == SpotState.nothing, "nothing said shows a note", &p)
        _ = spotting.tick(now: 44)
        expect(spotting.spot == nil, "the note goes after 4 s", &p)
        _ = spotting.termsNow(now: 0)
        _ = spotting.apply(.termsNow(TermsNow(ok: false, ids: [])), now: 50)
        expect(spotting.spot == .failed, "a failed search says so", &p)
        var ended = CallModel()
        expect(ended.termsNow(now: 0).isEmpty && ended.spot == nil, "no search outside a call", &p)
        expect(spotting.picker == nil, "with no lines said there is nothing to pick", &p)

        var picking = CallModel()
        _ = picking.apply(.ready(Ready(folder: "/tmp/x", replayT0: nil)), now: 0)
        let said = TranscriptLine(id: 1, segmentId: 1, channel: "them", lang: "he", text: "אנחנו על Okta Identity Governance",
                                  tStart: 1, speechEnd: 3, lineReady: 3, conversationLang: "he")
        _ = picking.apply(.line(said), now: 3)
        _ = picking.apply(.partial(Partial(channel: "them", text: "ועכשיו SailPoint", tStart: 4)), now: 5)
        expect(picking.termsNow(now: 6) == [.action("terms_now")] && picking.picker?.rows.count == 2,
               "the search opens the last line and the open sentence to pick from", &p)
        expect(picking.checkPicked().isEmpty, "nothing picked sends nothing", &p)
        for w in [2, 3, 4] { picking.togglePick(.init(row: 0, word: w), now: 7) }
        picking.togglePick(.init(row: 1, word: 1), now: 7)
        expect(picking.checkPicked() == [.checkTerms(["Okta Identity Governance", "SailPoint"])] && picking.picker == nil
               && picking.spot == .searching && picking.pickSent, "בדוק sends the picked phrases and closes", &p)
        _ = picking.apply(.term(okta), now: 8)
        _ = picking.apply(.termsNow(TermsNow(ok: true, ids: [])), now: 8)
        expect(picking.spot == .searching, "the search's own answer is left out after a pick", &p)
        _ = picking.apply(.termsNow(TermsNow(ok: true, ids: [2], picked: true)), now: 9)
        expect(picking.spot == nil && picking.freshTermId == 2, "the picked answer points at its term", &p)
        _ = picking.termsNow(now: 20)
        expect(!picking.pickSent && picking.picker != nil, "a new search can be picked again", &p)
        _ = picking.tick(now: 34.9)
        expect(picking.picker != nil, "the picker stays 15 s", &p)
        _ = picking.tick(now: 35)
        expect(picking.picker == nil, "with nothing picked it closes after 15 s", &p)
        _ = picking.apply(.termsNow(TermsNow(ok: true, ids: [])), now: 36)
        _ = picking.termsNow(now: 40)
        _ = picking.toggleChip(.terms)
        expect(picking.picker == nil, "מושגים off closes the picker", &p)
        _ = picking.toggleChip(.terms)
        picking.screenSent(now: 41)
        expect(picking.note?.title == "צילמתי את חלון השיחה", "a screenshot sent says so", &p)
        _ = picking.apply(.screen(ScreenAnswer(ok: true, text: "שקף על תמחור לפי משתמש")), now: 42)
        expect(picking.note?.title == "מהמסך" && picking.note?.detail == "שקף על תמחור לפי משתמש"
               && picking.note?.until == 54, "what the screen shows comes as a note for 12 s", &p)
        _ = picking.apply(.screen(ScreenAnswer(ok: false, text: "לא הצלחתי לקרוא את המסך")), now: 43)
        expect(picking.note?.title == "לא הצלחתי לקרוא את המסך" && picking.note?.detail == "", "a failed read says so", &p)
        let row = String(TermRow.line(soc).characters)
        expect(row == "\u{200F}SOC 2: תקן ביקורת אבטחה" && TextDirection.isRTL(soc.note), "Hebrew term row \(row)", &p)
        expect(String(TermRow.line(okta).characters).hasPrefix("\u{200E}Okta: "), "English term row", &p)
        let known = #"{"type": "term", "id": 3, "term": "Check Point", "note": "ענקית אבטחת רשת", "kind": "fund", "lang": "he", "trigger_line_ids": [4], "known": true, "ours": ["מאי 2026: כנס משותף", "מרץ 2026: שיחה"], "ours_links": ["https://example.com/a", ""], "file": "israel-cyber"}"#
        if let (event, _) = try? Event.decode(known), case .term(let entry) = event {
            expect(entry.known == true && entry.ours?.count == 2, "a known term decodes", &p)
            expect(TermRow.link(entry, 0)?.absoluteString == "https://example.com/a" && TermRow.link(entry, 1) == nil
                   && TermRow.link(entry, 2) == nil, "a שלנו line opens its source link, one without a link does not", &p)
            expect(TermRow.fromText(entry) == "מתוך israel-cyber", "a known name tells its file \(TermRow.fromText(entry) ?? "")", &p)
            var odd = entry
            odd.oursLinks = ["javascript:alert(1)", "file:///etc/hosts"]
            expect(TermRow.link(odd, 0) == nil && TermRow.link(odd, 1) == nil, "only web links open", &p)
            expect(termSymbol(entry) == "banknote", "fund symbol", &p)
            expect(TermRow.ours(entry) == "שלנו (2) · מאי 2026: כנס משותף", "ours line \(TermRow.ours(entry) ?? "")", &p)
            let one = Term(id: 4, term: "Wiz", note: "אבטחת ענן", lang: "he", triggerLineIds: [], known: true, ours: ["שיחה"])
            expect(TermRow.ours(one) == "שלנו · שיחה", "one ours line", &p)
        } else {
            p.append("a known term does not decode")
        }
        expect(TermRow.ours(soc) == nil && soc.known == nil, "an agent term has no ours line", &p)
        let older = #"{"type": "term", "id": 5, "term": "SOC 2", "note": "תקן", "kind": "term", "lang": "he", "trigger_line_ids": []}"#
        if let (event, _) = try? Event.decode(older), case .term(let plain) = event {
            expect(plain.known == nil && TermRow.ours(plain) == nil && TermRow.link(plain, 0) == nil
                   && TermRow.fromText(plain) == nil, "a term from an older engine has no ours line, link or file", &p)
        } else {
            p.append("a term from an older engine does not decode")
        }

        var opening = CallModel()
        opening.phase = .active
        let checkPoint = Term(id: 7, term: "Check Point", note: "ענקית אבטחת רשת", kind: "company", lang: "he",
                              triggerLineIds: [], known: true, ours: ["מאי 2026: כנס", "מרץ 2026: שיחה"])
        let team8 = Term(id: 8, term: "Team8", note: "קרן", kind: "fund", lang: "he", triggerLineIds: [], known: true, ours: [])
        for t in [checkPoint, team8, okta, soc] { _ = opening.apply(.term(t), now: 0) }
        expect(opening.toggleTerm(7).isEmpty && opening.openTermId == 7, "a click opens a known name with no request", &p)
        _ = opening.toggleTerm(8)
        expect(opening.openTermId == 8, "one row is open at a time", &p)
        _ = opening.toggleTerm(8)
        expect(opening.openTermId == nil, "a second click closes it", &p)
        expect(opening.toggleTerm(2) == [.explainTerm(2)] && opening.openTermId == 2 && opening.termDetails[2] == .asking,
               "a model term asks for its sentences", &p)
        expect(opening.toggleTerm(2).isEmpty && opening.openTermId == nil, "a second click closes it while it waits", &p)
        expect(opening.toggleTerm(2).isEmpty && opening.openTermId == 2, "opening it again does not ask again", &p)
        _ = opening.apply(.termDetail(TermDetail(id: 2, ok: true, text: "שירות התחברות לארגונים.")), now: 1)
        expect(opening.termDetails[2] == .ready("שירות התחברות לארגונים."), "the sentences arrive", &p)
        _ = opening.toggleTerm(2)
        expect(opening.toggleTerm(2).isEmpty && opening.openTermId == 2, "reopened, they show at once", &p)
        expect(opening.toggleTerm(1) == [.explainTerm(1)] && opening.openTermId == 1, "another row closes the open one", &p)
        _ = opening.apply(.termDetail(TermDetail(id: 1, ok: false, text: "")), now: 2)
        expect(opening.termDetails[1] == .failed && opening.openTermId == 1, "a failure stays open", &p)
        expect(opening.toggleTerm(1).isEmpty && opening.openTermId == nil, "a click on an open failure closes it", &p)
        expect(opening.toggleTerm(1) == [.explainTerm(1)] && opening.openTermId == 1 && opening.termDetails[1] == .asking,
               "opening a failure tries again", &p)
        _ = opening.apply(.termDetail(TermDetail(id: 1, ok: true, text: "  ")), now: 3)
        expect(opening.termDetails[1] == .failed, "empty sentences are a failure", &p)
        opening.phase = .ended
        expect(opening.toggleTerm(1).isEmpty && opening.openTermId == nil, "after the call a failure closes", &p)
        expect(opening.toggleTerm(1).isEmpty && opening.openTermId == 1 && opening.termDetails[1] == .failed,
               "after the call a failure opens with no request", &p)
        var after = CallModel()
        after.phase = .stopping
        _ = after.apply(.term(okta), now: 0)
        expect(after.toggleTerm(2).isEmpty && after.termDetails[2] == .failed && after.openTermId == 2,
               "after the call nothing is asked", &p)
        expect(after.toggleTerm(99).isEmpty && after.openTermId == 2, "a term that is not there", &p)

        var filed = CallModel()
        filed.phase = .active
        let detailed = Term(id: 5, term: "Wiz", note: "אבטחת ענן", kind: "company", lang: "he", triggerLineIds: [], known: true,
                            ours: [], detail: true)
        let bare = Term(id: 6, term: "Team8", note: "קרן", kind: "fund", lang: "he", triggerLineIds: [], known: true, ours: [])
        for t in [detailed, bare] { _ = filed.apply(.term(t), now: 0) }
        expect(filed.toggleTerm(6).isEmpty && filed.termDetails[6] == nil, "a known name with no details asks nothing", &p)
        expect(filed.toggleTerm(5) == [.explainTerm(5)] && filed.termDetails[5] == .asking,
               "a known name with details asks the engine, which answers from the file", &p)
        expect(!filed.canAskMore(5) && filed.askMore(5).isEmpty, "no more from the model before the details show", &p)
        let fileText = "תחום: אבטחת ענן\nהוקמה: 2020"
        _ = filed.apply(.termDetail(TermDetail(id: 5, ok: true, text: fileText)), now: 1)
        expect(filed.termDetails[5] == .ready(fileText) && filed.canAskMore(5) && !filed.canAskMore(6),
               "the file's details show with more from the model", &p)
        expect(filed.askMore(5) == [.explainMore(5)] && filed.termMore[5] == .asking && !filed.canAskMore(5),
               "more from the model asks once at a time", &p)
        _ = filed.apply(.termDetail(TermDetail(id: 5, ok: false, text: "לא הצלחתי להרחיב")), now: 2)
        expect(filed.termDetails[5] == .ready(fileText) && filed.termMore[5] == .failed && filed.canAskMore(5),
               "a failed more keeps the file's details and can be asked again", &p)
        expect(TermRow.moreNote(.failed) == L("לא הצלחתי להרחיב", "Could not explain it") && TermRow.moreNote(.added) == nil
               && TermRow.moreNote(.asking) == loadingText, "the note under the details", &p)
        _ = filed.askMore(5)
        let added = fileText + "\n\nהיא עלתה כי הם בוחנים אבטחת ענן."
        _ = filed.apply(.termDetail(TermDetail(id: 5, ok: true, text: added)), now: 3)
        expect(filed.termDetails[5] == .ready(added) && filed.termMore[5] == .added && !filed.canAskMore(5),
               "the model's answer comes under the details, and the button goes", &p)
        _ = filed.toggleTerm(5)
        expect(filed.toggleTerm(5).isEmpty && filed.termDetails[5] == .ready(added), "reopened, it shows at once", &p)
        var coachOff = filed
        coachOff.termMore[5] = nil
        coachOff.chips = Chips(on: [.transcript])
        expect(!coachOff.canAskMore(5) && coachOff.askMore(5).isEmpty, "no more from the model while the coach is off", &p)
        var afterCall = CallModel()
        afterCall.phase = .stopping
        _ = afterCall.apply(.term(detailed), now: 0)
        expect(afterCall.toggleTerm(5).isEmpty && afterCall.termDetails[5] == nil && !afterCall.canAskMore(5),
               "after the call a known name asks nothing and is no failure", &p)

        var stopping = CallModel()
        expect(stopping.pressStop(now: 0).isEmpty && stopping.stopConfirmUntil == nil, "no stop before the call", &p)
        stopping.phase = .active
        expect(stopping.pressStop(now: 1).isEmpty && stopping.stopConfirmUntil == 1 + CallModel.stopConfirmSeconds,
               "the first click opens the second step", &p)
        expect(stopping.confirmStop(summary: false) == [.stop(summary: false)] && stopping.stopConfirmUntil == nil,
               "stop without a summary", &p)
        var coachStop = CallModel()
        coachStop.phase = .active
        coachStop.coachEver = true
        _ = coachStop.pressStop(now: 1)
        expect(coachStop.confirmStop(summary: true) == [.stop(summary: true)], "stop and summarize", &p)
        expect(coachStop.confirmStop(summary: true).isEmpty, "no stop without the first click", &p)
        expect(CallModel.stopConfirmSeconds == 5, "five seconds to choose", &p)
        _ = stopping.pressStop(now: 20)
        _ = stopping.tick(now: 24.9)
        expect(stopping.stopConfirmUntil == 25, "the second step stays 5 s", &p)
        _ = stopping.tick(now: 25)
        expect(stopping.stopConfirmUntil == nil && stopping.confirmStop(summary: true).isEmpty, "then it closes by itself", &p)

        var noAccount = CallModel()
        noAccount.phase = .active
        noAccount.chips = Chips(on: [.transcript])
        _ = noAccount.toggleCoach()
        noAccount.present(EngineError(code: "no_agent_account", message: "no agent account is chosen", endsCall: false), now: 1)
        expect(!noAccount.chips.coach && !noAccount.coachEver && noAccount.banner != nil, "no account: the coach goes off and says why", &p)
        expect(noAccount.banner?.accounts == true && noAccount.banner?.detail == "",
               "no account: one line with the settings button, not the engine's English words", &p)
        expect(errorTitles["no_agent_account"] == L("אין חשבון פעיל למאמן", "No working account for the coach"), "its title", &p)
        expect(StatusLine.loading(phase: .active, transcribing: false) && !StatusLine.loading(phase: .active, transcribing: true)
               && !StatusLine.loading(phase: .stopping, transcribing: false), "טוען תמלול while the models load", &p)
        expect(StatusLine.agentTrouble("auth", coach: true) && !StatusLine.agentTrouble("auth", coach: false)
               && !StatusLine.agentTrouble("ok", coach: true), "the agent's dot shows only with the coach on", &p)
        expect(StopChoice.choices.map(\.summary) == [false, true], "the stop without a summary is first, the default", &p)
        expect(Chip.allCases.filter(ChipsRow.split(after:)) == [.transcript], "the chips' line is after תמלול, before the coach's", &p)
        expect(PanelMenu.keys.map(\.action.id) == ["ask_now", "terms_now", "screen", "mark"],
               "… lists the call's keys but hiding, which the X beside it does", &p)
        expect(PanelMenu.fillTitle(false) == "מסך מלא" && PanelMenu.fillTitle(true) == "חזרה לגודל הקודם",
               "the size item, its keys apart", &p)
        var finishing = CallModel()
        expect(PanelView.finishing(finishing) == PanelView.finishingText, "the finishing line of a recording", &p)
        finishing.coachEver = true
        expect(PanelView.finishing(finishing) == PanelView.finishingText, "a call the coach was in, ended with no stop, writes no summary", &p)
        finishing.stopSummary = true
        expect(PanelView.finishing(finishing) == PanelView.finishingSummaryText, "the finishing line follows the stop that was sent", &p)
        finishing.stopSummary = false
        expect(PanelView.finishing(finishing) == PanelView.finishingText, "a stop without a summary", &p)
        expect(SummaryReady(ok: false, path: "/c/summary.md", message: "בלי סיכום").stoppedWithoutSummary
               && !SummaryReady(ok: false, path: "/c/summary.md", message: "אין חשבון").stoppedWithoutSummary,
               "בלי סיכום is not a failure", &p)
        expect(AppState.chipsToRemember(noAccount) == nil, "a coach the engine refused leaves the remembered chips as they were", &p)

        var folded = CallModel()
        folded.goals = [Goal(id: "g1", text: "א", done: true), Goal(id: "g2", text: "ב", done: false), Goal(id: "g3", text: "ג", done: false)]
        expect(folded.goalCount == "מטרות 1/3" && !folded.goalsOpen, "goals fold into one line \(folded.goalCount)", &p)

        var goals = CallModel()
        goals.phase = .active
        goals.chips = Chips(on: [.ask])
        goals.goals = [Goal(id: "g1", text: "מי מחליט", done: false), Goal(id: "g2", text: "איך עושים היום", done: false)]
        expect(goals.focus("g2") == [.focusGoal("g2")] && goals.focusGoal == "g2", "שאל על זה puts a goal in focus", &p)
        expect(GoalsView.focusText(focused: false) == "שאל על זה" && GoalsView.focusText(focused: true) == "בפוקוס",
               "an open goal says שאל על זה, the goal in focus בפוקוס", &p)
        expect(goals.focus("g2") == [.focusGoal(nil)] && goals.focusGoal == nil, "pressed again, the focus ends", &p)
        _ = goals.focus("g1")
        expect(goals.toggleGoal("g1") == [.setGoal("g1", true)] && goals.goals[0].done && goals.focusGoal == nil,
               "a goal checked by hand ends its focus", &p)
        expect(goals.focus("g1").isEmpty, "a goal that is done takes no focus", &p)
        _ = goals.focus("g2")
        _ = goals.apply(.goals([Goal(id: "g1", text: "מי מחליט", done: true), Goal(id: "g2", text: "איך עושים היום", done: true)]), now: 1)
        expect(goals.focusGoal == nil, "a goal the coach closed ends its focus", &p)
        var focusOff = CallModel()
        focusOff.phase = .active
        focusOff.chips = Chips(on: [.transcript])
        focusOff.goals = [Goal(id: "g1", text: "מי מחליט", done: false)]
        expect(focusOff.focus("g1") == [.setCoach(true), .setSections(Sections(ask: true, terms: false)), .focusGoal("g1")],
               "focus in a recording turns on questions first", &p)

        expect(kindSymbols.keys.allSatisfy { kindLabels[$0] != nil }, "every card kind has a label", &p)
        expect(["tip", "steer", "question"].map { kindLabels[$0] ?? "" } == ["טיפ", "חזרה למטרה", "שאלה"]
               && kindLabels["insight"] == nil && kindSymbols["insight"] == nil, "card labels \(kindLabels)", &p)
        expect(Chip.allCases.map(\.title) == ["כתוביות", "תרגום", "סיכום ביניים", "שאלות", "מושגים"], "chip titles", &p)
        expect(Usage.tokens(950) == "950" && Usage.tokens(18_400) == "18.4K" && Usage.tokens(240_000) == "240K",
               "token counts read short", &p)
        let usageRows = Usage.rows(["other": 10, "translate": 0, "live": 400, "explain": 100])
        expect(usageRows.map(\.tokens) == [400, 100, 10] && usageRows.first?.share == 1
               && usageRows.first?.name == Usage.name("live"), "the usage rows: features that used tokens, live first", &p)
        expect(LimitBanner.text("near", coach: true) != nil && LimitBanner.text("near", coach: false) == nil
               && LimitBanner.text("reached", coach: false) != nil && LimitBanner.text("ok", coach: true) == nil,
               "the limit banner: near only with the agent on, reached always", &p)
        let usageStatus = #"{"type": "status", "t": 1, "state": "active", "elapsed": 3, "talk_share": 0, "lang_me": "he", "lang_them": "he", "conversation_lang": "he", "agent": "ok", "level": "normal", "visible": true, "tokens": 1200, "features": {"live": 1100, "other": 100}, "cpu": 14.5, "limit": "near"}"#
        if let (event, _) = try? Event.decode(usageStatus), case .status(let st) = event {
            expect(st.tokens == 1200 && st.features?["live"] == 1100 && st.cpu == 14.5 && st.limit == "near",
                   "the status carries the usage, CPU and limit", &p)
        } else {
            p.append("a status with usage does not decode")
        }

        let fresh = CallModel.startChips(recording: false, transcript: false, remembered: nil)
        expect(fresh.on == [.ask, .terms] && fresh.coach, "a first call with the coach: questions and terms", &p)
        let remembered = CallModel.startChips(recording: false, transcript: false, remembered: [.translate, .recap])
        expect(remembered.on == [.translate, .recap, .transcript], "the chips of the last coach call, translation brings the transcript", &p)
        let emptyMemory = CallModel.startChips(recording: false, transcript: true, remembered: [])
        expect(emptyMemory.on == [.transcript, .ask, .terms], "a last call that left no AI chip falls back", &p)
        let rec = CallModel.startChips(recording: true, transcript: true, remembered: [.translate, .ask])
        expect(rec.on == [.transcript] && !rec.coach, "a recording starts with no AI, translation never", &p)

        var recording = CallModel()
        recording.phase = .active
        recording.chips = Chips(on: [.transcript])
        expect(recording.toggleChip(.translate) == [.setCoach(true), .setSections(Sections(ask: false, terms: false))]
               && recording.chips.on == [.transcript, .translate] && recording.coachEver, "an AI chip in a recording turns the coach on with it alone", &p)
        expect(recording.toggleChip(.transcript) == [.setCoach(false)] && recording.chips.on.isEmpty,
               "the transcript off takes the translation with it, and the coach goes off", &p)
        expect(recording.toggleChip(.translate).first == .setCoach(true) && recording.chips.on == [.translate, .transcript],
               "translation turns the transcript on", &p)
        var recapOnly = CallModel()
        recapOnly.phase = .active
        recapOnly.chips = Chips(on: [])
        expect(recapOnly.toggleChip(.recap) == [.setCoach(true), .setSections(Sections(ask: false, terms: false, now: true))]
               && recapOnly.chips[.recap], "the recap chip in a recording turns the coach on and asks for the now line", &p)
        var nowOff = CallModel()
        nowOff.phase = .active
        nowOff.chips = Chips(on: [])
        nowOff.nowLineOn = false
        expect(nowOff.toggleChip(.recap) == [.setCoach(true), .setSections(Sections(ask: false, terms: false, now: false))],
               "with the now line off the recap chip asks nothing itself", &p)
        expect(nowOff.toggleNowLine() == [.setSections(Sections(ask: false, terms: false, now: true))] && nowOff.nowLineOn,
               "the now switch tells the engine at once", &p)
        _ = nowOff.apply(.topic(Topic(text: " דנה מסבירה איך עובדות בקשות גישה ", triggerLineIds: [3])), now: 1)
        expect(nowOff.nowLine == "דנה מסבירה איך עובדות בקשות גישה", "a topic shows trimmed", &p)
        _ = nowOff.apply(.topic(Topic(text: "", triggerLineIds: [])), now: 2)
        expect(nowOff.nowLine == "דנה מסבירה איך עובדות בקשות גישה", "an empty topic keeps the shown line", &p)
        expect(nowOff.toggleNowLine() == [.setSections(Sections(ask: false, terms: false, now: false))] && nowOff.nowLine == nil,
               "the now switch off forgets the line", &p)
        _ = nowOff.apply(.topic(Topic(text: "מדברים על התקציב", triggerLineIds: [4])), now: 3)
        expect(nowOff.nowLine == nil, "a topic that comes while the line is off is dropped", &p)
        _ = nowOff.toggleNowLine()
        _ = nowOff.apply(.topic(Topic(text: "מדברים על התקציב", triggerLineIds: [4])), now: 4)
        expect(nowOff.toggleChip(.recap) == [.setSections(Sections(ask: false, terms: false, now: false)), .setCoach(false)]
               && nowOff.nowLine == nil && nowOff.nowLineOn, "the recap closed forgets the line and keeps its switch", &p)
        var idleNow = CallModel()
        expect(idleNow.toggleNowLine().isEmpty && !idleNow.nowLineOn, "the switch before a call only remembers", &p)
        var listing = CallModel()
        listing.phase = .active
        listing.chips = Chips(on: [.terms])
        expect(listing.toggleListOnly() == [.setSections(Sections(ask: false, terms: true, listOnly: true))] && listing.listOnly,
               "רק מהרשימה tells the engine at once", &p)
        expect(listing.toggleChip(.ask) == [.setSections(Sections(ask: true, terms: true, listOnly: true))],
               "and goes with every sections the chips send", &p)
        _ = listing.toggleChip(.terms)
        expect(listing.sections == Sections(ask: true, terms: false, listOnly: false) && listing.listOnly,
               "with מושגים off it is not sent, and it is kept for when they are on again", &p)
        expect(listing.toggleListOnly().isEmpty && !listing.listOnly, "while מושגים is off the switch tells the engine nothing", &p)

        var coach = CallModel()
        coach.phase = .active
        coach.chips = Chips(on: [.ask, .terms])
        coach.coachEver = true
        expect(coach.toggleCoach() == [.setSections(Sections(ask: false, terms: false)), .setCoach(false)] && !coach.chips.coach,
               "מאמן off turns every AI chip off", &p)
        expect(coach.toggleCoach() == [.setCoach(true), .setSections(Sections(ask: true, terms: true))] && coach.chips.on == [.ask, .terms],
               "and on brings back the ones that were on", &p)
        _ = coach.toggleChip(.ask)
        _ = coach.toggleChip(.terms)
        expect(!coach.chips.coach && coach.lastCoachChips == [.terms]
               && coach.toggleCoach(remembered: [.recap]) == [.setCoach(true), .setSections(Sections(ask: false, terms: true))],
               "the chips of the last time in this call come before the remembered ones", &p)
        var never = CallModel()
        never.phase = .active
        never.chips = Chips(on: [])
        expect(never.toggleCoach(remembered: nil) == [.setCoach(true), .setSections(Sections(ask: true, terms: true))],
               "a recording that never had the coach starts it with questions and terms", &p)
        var rememberedCoach = CallModel()
        rememberedCoach.phase = .active
        rememberedCoach.chips = Chips(on: [])
        expect(rememberedCoach.toggleCoach(remembered: [.translate]) == [.setCoach(true), .setSections(Sections(ask: false, terms: false))]
               && rememberedCoach.chips.on == [.translate, .transcript], "or with the chips of the last call with the coach", &p)
        var idle = CallModel()
        expect(idle.toggleChip(.ask).isEmpty && idle.toggleCoach().isEmpty && idle.chips.on == [.ask, .terms],
               "no chip changes before the call", &p)

        var sectioned = CallModel()
        expect(sectioned.chips == Chips(on: [.ask, .terms]) && sectioned.hearing, "a call starts hearing, with questions and terms on", &p)
        sectioned.phase = .active
        _ = sectioned.apply(.questions(Questions(items: [Question(text: "Who signs off?", he: "מי מאשר?")], lang: "en",
                                                 triggerLineIds: [])), now: 0)
        let first = sectioned.apply(.card(card(1, he: "")), now: 0)
        _ = sectioned.apply(.asked(Asked(text: "How is yours different?", he: "במה שלכם שונה?")), now: 0)
        expect(first == [.shown(cardId: 1, t: 0)] && sectioned.cards.current?.id == 1 && sectioned.shownAsked != nil
               && sectioned.questions.map(\.he) == ["מי מאשר?"], "the sections that are on show what comes", &p)
        expect(sectioned.toggleChip(.ask) == [.setSections(Sections(ask: false, terms: true))],
               "a chip off tells the engine", &p)
        expect(sectioned.questions.isEmpty && !sectioned.liveQuestions && sectioned.cards.current == nil && sectioned.asked == nil,
               "שאלות off forgets its questions, card and asked question", &p)
        let late = sectioned.apply(.card(card(2)), now: 30)
        _ = sectioned.apply(.questions(Questions(items: [Question(text: "מה הלאה?")], lang: "he", triggerLineIds: [])), now: 30)
        _ = sectioned.apply(.asked(Asked(text: "ומה המחיר?")), now: 30)
        expect(late.isEmpty && sectioned.cards.current == nil && sectioned.questions.isEmpty && sectioned.asked == nil,
               "שאלות off takes nothing that still comes", &p)
        _ = sectioned.toggleChip(.ask)
        expect(sectioned.chips[.ask] && sectioned.questions.isEmpty && sectioned.cards.current == nil,
               "on again it is empty until the next request", &p)
        _ = sectioned.apply(.term(Term(id: 1, term: "Okta", note: "התחברות", lang: "he", triggerLineIds: [])), now: 31)
        expect(sectioned.toggleChip(.terms) == [.setSections(Sections(ask: true, terms: false))]
               && sectioned.terms.count == 1, "מושגים off keeps the call's terms", &p)
        expect(sectioned.toggleChip(.recap) == [.setSections(Sections(ask: true, terms: false, now: true))] && sectioned.chips[.recap],
               "the recap chip tells the engine only of the now line, refreshRecap asks", &p)
        expect(sectioned.toggleChip(.recap) == [.setSections(Sections(ask: true, terms: false))] && !sectioned.chips[.recap]
               && sectioned.sections == Sections(ask: true, terms: false), "the recap closes the now line with it", &p)

        var hearingModel = CallModel()
        hearingModel.phase = .active
        func heard(_ on: Bool, voice: Bool) -> Event {
            .status(Status(state: "active", elapsed: 1, talkShare: 0.3, langMe: "he", langThem: "he", conversationLang: "he",
                           agent: "ok", level: "normal", visible: true, hearing: on, meVoice: voice))
        }
        _ = hearingModel.apply(heard(true, voice: true), now: 0)
        _ = hearingModel.apply(.asked(Asked(text: "ומי מאשר?")), now: 0)
        expect(hearingModel.meVoice && hearingModel.shownAsked?.text == "ומי מאשר?", "me_voice counts while it hears my voice", &p)
        expect(hearingModel.toggleHearing(now: 10) == [.setHearing(false)] && !hearingModel.hearing && hearingModel.asked == nil,
               "a click turns hearing off at once and drops the asked question", &p)
        _ = hearingModel.apply(heard(true, voice: true), now: 11)
        expect(!hearingModel.hearing && !hearingModel.meVoice, "a status from before the click does not undo it", &p)
        _ = hearingModel.apply(heard(false, voice: false), now: 12)
        _ = hearingModel.apply(.asked(Asked(text: "עוד שאלה?")), now: 12)
        expect(!hearingModel.hearing && hearingModel.hearingClick == nil && hearingModel.asked == nil,
               "the engine agrees, and no asked question while it does not hear me", &p)
        expect(hearingModel.toggleHearing(now: 20) == [.setHearing(true)] && hearingModel.hearing, "a second click turns it on", &p)
        _ = hearingModel.apply(heard(false, voice: false), now: 24)
        expect(!hearingModel.hearing, "after 3 s the engine's value is taken", &p)
        var notYet = CallModel()
        expect(notYet.toggleHearing(now: 0).isEmpty && notYet.hearing, "no hearing switch before the call", &p)

        var pausing = CallModel()
        pausing.phase = .active
        func pausedStatus(_ on: Bool?, voice: Bool = false) -> Event {
            .status(Status(state: "active", elapsed: 1, talkShare: 0.3, langMe: "he", langThem: "he", conversationLang: "he",
                           agent: "ok", level: "normal", visible: true, hearing: true, meVoice: voice, paused: on))
        }
        _ = pausing.apply(pausedStatus(false, voice: true), now: 0)
        _ = pausing.apply(.asked(Asked(text: "ומי מאשר?")), now: 0)
        _ = pausing.apply(.partial(Partial(channel: "them", text: "אז בעצם", tStart: 0)), now: 0)
        expect(!pausing.paused && pausing.meVoice && pausing.partial != nil, "a call starts recording", &p)
        expect(pausing.togglePause(now: 10) == [.setPaused(true)] && pausing.paused && pausing.asked == nil
               && pausing.partial == nil && !pausing.meVoice, "a click pauses at once and drops what was being heard", &p)
        _ = pausing.apply(pausedStatus(false, voice: true), now: 11)
        _ = pausing.apply(.asked(Asked(text: "עוד שאלה?")), now: 11)
        expect(pausing.paused && pausing.shownAsked == nil && !pausing.meVoice,
               "a status from before the click does not undo the pause, and nothing is asked while paused", &p)
        _ = pausing.apply(pausedStatus(true), now: 12)
        expect(pausing.paused && pausing.pausedClick == nil, "the engine agrees", &p)
        expect(pausing.togglePause(now: 20) == [.setPaused(false)] && !pausing.paused, "a second click goes on", &p)
        _ = pausing.apply(pausedStatus(nil), now: 21)
        _ = pausing.apply(pausedStatus(true), now: 24)
        expect(pausing.paused, "after 3 s the engine's value is taken \(pausing.paused)", &p)
        var noPaused = CallModel()
        noPaused.phase = .active
        _ = noPaused.apply(pausedStatus(nil), now: 0)
        expect(!noPaused.paused && noPaused.togglePause(now: 0) == [.setPaused(true)], "an older engine sends no paused", &p)
        var unstarted = CallModel()
        expect(unstarted.togglePause(now: 0).isEmpty && !unstarted.paused, "no pause before the call", &p)
        expect(unstarted.askNow(now: 0).isEmpty && unstarted.askingUntil == nil, "no question before the call", &p)

        var hiding = CallModel()
        hiding.phase = .active
        for (id, name) in [(1, "Okta"), (2, "SCIM")] {
            _ = hiding.apply(.term(Term(id: id, term: name, note: "מושג", lang: "he", triggerLineIds: [])), now: 0)
        }
        _ = hiding.toggleTerm(1)
        hiding.hideTerm(1, saved: true, now: 1)
        expect(hiding.shownTerms.map(\.id) == [2] && hiding.openTermId == nil && hiding.freshTermId == 2
               && hiding.note?.title == "לא אקפיץ יותר את Okta", "a row marked as known goes at once", &p)
        _ = hiding.apply(.termsNow(TermsNow(ok: true, ids: [1])), now: 2)
        expect(hiding.freshTermId == 2 && hiding.spot == SpotState.nothing, "a search does not point at a hidden row", &p)
        hiding.hideTerm(2, saved: false, now: 3)
        expect(hiding.shownTerms.isEmpty && hiding.note?.title == "לא הצלחתי לשמור ברשימה", "not saved, it says so", &p)

        var asking = CallModel()
        asking.phase = .active
        asking.chips = Chips(on: [.transcript])
        expect(asking.askNow(now: 1) == [.setCoach(true), .setSections(Sections(ask: true, terms: false)), .action("ask_now")]
               && asking.askingUntil == 13, "a question now in a recording turns on questions first", &p)
        var foldedAsk = CallModel()
        foldedAsk.phase = .active
        foldedAsk.chips = Chips(on: [])
        foldedAsk.folded = true
        var foldedSearch = foldedAsk
        _ = foldedAsk.askNow(now: 0)
        _ = foldedSearch.termsNow(now: 0)
        expect(!foldedAsk.folded && foldedAsk.chips[.ask] && !foldedSearch.folded && foldedSearch.chips[.terms],
               "a question or a search now in a folded recording unfolds the panel, so the answer can be read", &p)
        var foldedOn = CallModel()
        foldedOn.phase = .active
        foldedOn.folded = true
        _ = foldedOn.askNow(now: 0)
        expect(foldedOn.folded, "with שאלות already on the panel stays as it is", &p)
        _ = asking.apply(.card(card(1)), now: 1)
        expect(asking.askingUntil != nil, "a nudge of its own is not the answer", &p)
        _ = asking.apply(.card(card(2, forced: true)), now: 2)
        expect(asking.askingUntil == nil && asking.cards.current?.id == 2, "the answer ends the wait", &p)
        _ = asking.askNow(now: 10)
        _ = asking.tick(now: 22)
        expect(asking.askingUntil == nil, "with no answer the button comes back after 12 s", &p)

        var steering = CallModel()
        steering.phase = .active
        steering.liveQuestions = true
        let raised = Question(text: "Who raised the first round?", he: "מי גייס את הסבב הראשון?")
        let surprised = Question(text: "מה הכי הפתיע אותך?", he: "")
        steering.questions = [raised, surprised]
        expect(steering.dropQuestion(raised) && steering.questions == [surprised] && !steering.dropQuestion(raised),
               "לא בכיוון takes the question off at once, once", &p)
        let asked = Card(id: 41, kind: "question", text: "מי מחליט?", lang: "he", triggerLineIds: [], requestedAt: 0,
                         receivedAt: 0, forced: true)
        let tip = Card(id: 42, kind: "tip", text: "תבקש דוגמה", lang: "he", triggerLineIds: [], requestedAt: 0,
                       receivedAt: 0, forced: false)
        _ = steering.cards.add(asked, now: 1, visible: true)
        expect(!steering.dropCard(tip) && steering.dropCard(asked) && steering.cards.current == nil && !steering.dropCard(asked),
               "לא בכיוון takes a question I asked for off the slot, once, and never a tip", &p)
        steering.noted(now: 5)
        expect(steering.note?.title == "המאמן עודכן" && steering.note?.detail == "השאלות יתחלפו בהתאם" && steering.note?.until == 10,
               "a note says the coach got it", &p)
        steering.menuOpen = true
        steering.toggleFold()
        expect(steering.folded && !steering.menuOpen, "folding closes the menu", &p)
        steering.toggleFold()
        expect(!steering.folded, "a second click unfolds", &p)
        var over = steering
        over.phase = .ended
        expect(!over.dropQuestion(surprised), "no לא בכיוון after the call", &p)

        var recap = CallModel()
        recap.phase = .active
        recap.chips = Chips(on: [])
        expect(recap.toggleChip(.recap).contains(.setCoach(true)) && recap.refreshRecap().isEmpty && recap.recap == .empty,
               "before anything was said it asks nothing", &p)
        _ = recap.apply(.line(TranscriptLine(id: 1, segmentId: 1, channel: "them", lang: "he", text: "דנה מספרת על סלאק", tStart: 0,
                                             speechEnd: 1, lineReady: 1, conversationLang: "he")), now: 1)
        expect(recap.refreshRecap() == [.recap] && recap.recap == .asking, "the first update covers the whole call", &p)
        expect(recap.refreshRecap().isEmpty, "one request at a time", &p)
        _ = recap.apply(.recap(Recap(ok: true, text: "- דנה מספרת על סלאק\n\n• מפתח חיכה יומיים\nא\nב\nג\nד")), now: 2)
        let five: RecapState = .ready(["דנה מספרת על סלאק", "מפתח חיכה יומיים", "א", "ב", "ג"])
        expect(recap.recap == five, "up to five lines, bullets taken off \(String(describing: recap.recap))", &p)
        expect(recap.refreshRecap() == [.recapSince] && recap.recapUpdate == .asking, "after it, only what is new since", &p)
        expect(recap.refreshRecap().isEmpty && recap.updateRecap().isEmpty, "one update at a time", &p)
        _ = recap.apply(.recap(Recap(ok: true, text: "- מארק ביקש הצעת מחיר\nעד מחר", since: "14:05")), now: 3)
        expect(recap.recap == five && recap.recapParts == [RecapPart(since: "14:05", lines: ["מארק ביקש הצעת מחיר", "עד מחר"])]
               && recap.recapUpdate == nil, "an update is added under what was there", &p)
        expect(recap.refreshRecap() == [.recapSince], "the arrow asks again", &p)
        _ = recap.apply(.recap(Recap(ok: true, text: "", since: "15:10")), now: 4)
        expect(recap.recapUpdate == .nothingNew && recap.recapParts.count == 1, "nothing new adds nothing", &p)
        _ = recap.refreshRecap()
        _ = recap.apply(.recap(Recap(ok: false, text: "לא הצלחתי לסכם", since: "15:10")), now: 5)
        expect(recap.recapUpdate == .failed && recap.recap == five && recap.recapParts.count == 1,
               "a failed update keeps what was there", &p)
        _ = recap.apply(.recap(Recap(ok: false, text: "")), now: 6)
        expect(recap.recap == five, "a newer one that fails keeps the last one", &p)
        _ = recap.apply(.recap(Recap(ok: true, text: "מארק ביקש הזמנה")), now: 7)
        expect(recap.recap == .ready(["מארק ביקש הזמנה"]), "a newer one replaces it", &p)
        var foldedRecap = recap
        foldedRecap.recapUpdate = nil
        foldedRecap.toggleFold()
        expect(foldedRecap.refreshRecap().isEmpty, "a folded panel asks nothing", &p)
        var hiddenRecap = recap
        hiddenRecap.recapUpdate = nil
        _ = hiddenRecap.setVisible(false, now: 8)
        expect(hiddenRecap.refreshRecap().isEmpty, "a hidden panel asks nothing", &p)
        var recapOff = recap
        recapOff.recapUpdate = nil
        _ = recapOff.toggleChip(.recap)
        expect(recapOff.refreshRecap().isEmpty && recapOff.recap == .ready(["מארק ביקש הזמנה"]),
               "with the chip off nothing is asked and what it had is kept", &p)
        var ending = recap
        ending.recapUpdate = nil
        ending.phase = .stopping
        expect(ending.refreshRecap().isEmpty && ending.updateRecap().isEmpty, "no update once the call is ending", &p)
        var failing = CallModel()
        failing.phase = .active
        failing.chips = Chips(on: [.recap])
        _ = failing.apply(.line(TranscriptLine(id: 1, segmentId: 1, channel: "them", lang: "he", text: "שלום", tStart: 0,
                                               speechEnd: 1, lineReady: 1, conversationLang: "he")), now: 1)
        expect(failing.lineCount == 1, "a line counts once", &p)
        _ = failing.apply(.line(TranscriptLine(id: 1, segmentId: 1, channel: "them", lang: "he", text: "שלום עולם", tStart: 0,
                                               speechEnd: 1, lineReady: 1, conversationLang: "he")), now: 1)
        expect(failing.lineCount == 1, "a line that arrives again counts once", &p)
        _ = failing.refreshRecap()
        _ = failing.apply(.recap(Recap(ok: false, text: "")), now: 2)
        expect(failing.recap == .failed, "a failure shows", &p)
        expect(failing.refreshRecap() == [.recap] && failing.recap == .asking, "the next refresh tries again", &p)
        _ = failing.apply(.recap(Recap(ok: true, text: " \n ")), now: 3)
        expect(failing.recap == .failed, "an empty answer is a failure", &p)
        expect(failing.askRecap() == [.recap] && failing.recap == .asking, "a click on the failure tries again", &p)
        failing.phase = .ended
        expect(failing.refreshRecap().isEmpty && failing.askRecap().isEmpty && failing.recap == .failed,
               "after the call nothing is asked", &p)

        func partial(_ text: String, _ start: Double, channel: String = "them") -> Event {
            .partial(Partial(channel: channel, text: text, tStart: start))
        }
        func line(_ id: Int, _ start: Double, channel: String = "them") -> Event {
            .line(TranscriptLine(id: id, segmentId: id, channel: channel, lang: "he", text: "שורה \(id)", tStart: start,
                                 speechEnd: start + 2, lineReady: start + 3, conversationLang: "he"))
        }
        var liveLine = CallModel()
        _ = liveLine.apply(partial("אנחנו עובדים עם", 100), now: 102)
        expect(liveLine.partial?.text == "אנחנו עובדים עם", "a partial shows", &p)
        _ = liveLine.apply(line(1, 95, channel: "me"), now: 103)
        _ = liveLine.apply(line(2, 90), now: 103)
        expect(liveLine.partial != nil, "a line of another sentence or side keeps the partial", &p)
        _ = liveLine.apply(partial("אנחנו עובדים עם Okta", 100), now: 104)
        _ = liveLine.apply(line(3, 100.05), now: 105)
        expect(liveLine.partial == nil && liveLine.recentLines.last?.id == 3, "the line of the sentence replaces it", &p)
        _ = liveLine.apply(partial("אנחנו עובדים עם Okta כבר", 100), now: 105.5)
        expect(liveLine.partial == nil, "a partial that comes after its line is not shown", &p)
        var translated = CallModel()
        _ = translated.apply(line(1, 10), now: 20)
        _ = translated.apply(.translation(Translation(id: 1, he: "שורה בעברית")), now: 21)
        _ = translated.apply(.translation(Translation(id: 7, he: "לא בפאנל")), now: 21)
        expect(translated.translations == [1: "שורה בעברית"], "a translation is kept for a line on the panel \(translated.translations)", &p)
        _ = translated.apply(line(2, 20), now: 22)
        _ = translated.apply(line(3, 30), now: 23)
        expect(translated.translations.isEmpty, "a line that leaves the panel takes its translation along", &p)
        var recorder = CallModel()
        recorder.chips = Chips(on: [.transcript])
        for id in 1...9 { _ = recorder.apply(line(id, Double(id) * 10), now: 200) }
        expect(recorder.recentLines.map(\.id) == Array(4...9), "with no AI chip on more lines are kept \(recorder.recentLines.map(\.id))", &p)
        recorder.phase = .active
        recorder.translations = [4: "ארבע", 9: "תשע"]
        _ = recorder.toggleChip(.translate)
        expect(recorder.recentLines.map(\.id) == [8, 9] && recorder.translations == [9: "תשע"],
               "the coach on keeps only the lines its transcript shows \(recorder.recentLines.map(\.id))", &p)
        _ = liveLine.apply(partial("ועוד משהו", 110), now: 112)
        _ = liveLine.apply(partial("ועוד משהו על", 110), now: 114)
        _ = liveLine.tick(now: 121.9)
        expect(liveLine.partial != nil, "a partial stays 8 s after its last update", &p)
        _ = liveLine.tick(now: 122)
        expect(liveLine.partial == nil, "then it goes", &p)
        _ = liveLine.apply(partial("", 130), now: 130)
        expect(liveLine.partial == nil, "an empty partial is not shown", &p)
        expect(TranscriptRows.live("אנחנו עובדים") == "\u{200F}…אנחנו עובדים" && TranscriptRows.live("We use") == "\u{200E}…We use",
               "the live line starts with its direction and an ellipsis", &p)

        for (text, rtl) in [("היי דנה, מה שלומך?", true), ("Hi everyone", false),
                            ("SOC 2: תקן ביקורת אבטחה. צריך להוכיח מי קיבל גישה.", true),
                            ("What would SOC 2 mean for you?", false), ("12:30", false),
                            ("ניסיון קודם: מרק אמר \"we bought Nimbus Labs last year, but nobody uses it\"", true)] {
            expect(TextDirection.isRTL(text) == rtl, "direction of \(text)", &p)
            expect(TextDirection.marked(text).unicodeScalars.first == (rtl ? "\u{200F}" : "\u{200E}"),
                   "base direction mark of \(text)", &p)
            expect(TextDirection.marked(TextDirection.marked(text)) == TextDirection.marked(text), "one mark only on \(text)", &p)
        }

        let stream = (try? String(contentsOf: root.appendingPathComponent("tests/protocol/stream/demo-call.jsonl"),
                                  encoding: .utf8)) ?? ""
        var model = CallModel()
        var shown: [Int] = []
        var agents: [String] = []
        var liveTexts: [String] = []
        var hearings: [Bool] = []
        var askedTexts: [String] = []
        var voices = 0
        for line in stream.split(separator: "\n") {
            guard let (event, t) = try? Event.decode(String(line)) else { continue }
            let now = t ?? 0
            for case .shown(let id, _) in model.apply(event, now: now) + model.tick(now: now) { shown.append(id) }
            if let text = model.partial?.text, liveTexts.last != text { liveTexts.append(text) }
            if agents.last != model.agent { agents.append(model.agent) }
            if hearings.last != model.hearing { hearings.append(model.hearing) }
            if let asked = model.shownAsked?.text, askedTexts.last != asked { askedTexts.append(asked) }
            if model.meVoice { voices += 1 }
        }
        expect(model.phase == .ended, "the stream ends the call", &p)
        expect(shown == [1, 2, 3, 4, 5], "cards shown \(shown)", &p)
        expect(model.terms.map(\.term) == ["SOC 2"], "terms \(model.terms.map(\.term))", &p)
        expect(model.liveQuestions && model.questions.map(\.text) == ["מה קרה בפעם האחרונה?", "מי צריך לאשר גישה?"]
               && model.questions.allSatisfy(\.he.isEmpty), "live questions \(model.questions)", &p)
        expect(hearings == [true, false, true] && askedTexts == ["How is yours different?"] && model.asked == nil && voices > 0,
               "the stream's hearing \(hearings) and asked question \(askedTexts)", &p)
        if case .ready(let recapLines)? = model.recap {
            expect(recapLines.count == 3, "the stream's recap \(recapLines)", &p)
        } else {
            p.append("the stream's recap did not arrive")
        }
        expect(model.recentLines.map(\.id) == [9, 10], "recent lines \(model.recentLines.map(\.id))", &p)
        expect(model.goalProgress == "3 מתוך 3", "goal progress \(model.goalProgress)", &p)
        LanguageStore.shared.lang = .en
        expect(model.goalProgress == "3 of 3", "English goal progress \(model.goalProgress)", &p)
        expect(errorTitles["auto_stop"] == "No speech for 5 minutes, the call ended", "English error title", &p)
        expect(errorTitles.values.allSatisfy { $0.unicodeScalars.allSatisfy { !(0x0590...0x05FF).contains($0.value) } },
               "every error title has English", &p)
        var english = CallModel()
        english.present(EngineError(code: "new_code", message: "m", endsCall: false), now: 0)
        expect(english.banner?.title == "Error", "English fallback title \(english.banner?.title ?? "")", &p)
        LanguageStore.shared.lang = .he
        expect(agents == ["off", "ok", "unavailable", "ok"], "agent states \(agents)", &p)
        expect(model.summary?.ok == true && model.usage?.month.requests == 44 && model.finalized?.ok == true,
               "summary, usage and finalized", &p)
        expect(model.lastLine?.id == 10, "last line", &p)
        expect(liveTexts.count == 1 && model.partial == nil, "the stream's partial shows until its line \(liveTexts)", &p)

        var early = CallModel()
        early.phase = .warming
        early.present(EngineError(code: "transcription_start_failed", message: "x", endsCall: false), now: 100)
        early.present(EngineError(code: "capture", message: "y", endsCall: false), now: 100)
        _ = early.tick(now: 300)
        expect(early.banner != nil && early.note != nil, "notices before the call wait for it", &p)
        early.phase = .active
        _ = early.tick(now: 400)
        _ = early.tick(now: 404)
        expect(early.banner != nil && early.note != nil, "notices count from the start of the call", &p)
        _ = early.tick(now: 405)
        expect(early.banner != nil && early.note == nil, "the note goes after 5 s of the call", &p)
        _ = early.tick(now: 429)
        expect(early.banner != nil, "the banner stays 30 s of the call", &p)
        _ = early.tick(now: 430)
        expect(early.banner == nil, "the banner goes after 30 s of the call", &p)
        var live = CallModel()
        live.phase = .active
        live.present(EngineError(code: "source", message: "z", endsCall: false), now: 50)
        _ = live.tick(now: 80)
        expect(live.banner == nil, "a banner in the call goes after 30 s", &p)

        let codes = errorCodes(root)
        expect(codes.count > 10, "errors table has \(codes.count) codes", &p)
        for code in codes {
            expect(errorPresentation[code] != nil && (errorPresentation[code] == .log || errorTitles[code] != nil),
                   "no presentation for \(code)", &p)
        }
        return p
    }

    static func errorCodes(_ root: URL) -> [String] {
        let doc = (try? String(contentsOf: root.appendingPathComponent("docs/protocol.md"), encoding: .utf8)) ?? ""
        guard let start = doc.range(of: "## Errors") else { return [] }
        let rest = doc[start.upperBound...]
        let section = rest.range(of: "\n## ").map { rest[..<$0.lowerBound] } ?? rest
        return section.split(separator: "\n").compactMap { row -> String? in
            guard row.hasPrefix("| `") else { return nil }
            return row.dropFirst(3).split(separator: "`").first.map(String.init)
        }.filter { !$0.isEmpty && !$0.contains(" ") }
    }
}
