import Foundation

let protocolVersion = 6

struct Goal: Codable, Equatable, Identifiable {
    var id: String
    var text: String
    var done: Bool
}

struct Totals: Codable, Equatable {
    var input, output, cacheRead, cacheWrite, requests: Int
    var usd: Double
    static let zero = Totals(input: 0, output: 0, cacheRead: 0, cacheWrite: 0, requests: 0, usd: 0)
}

struct Status: Codable, Equatable {
    let state: String
    let elapsed, talkShare: Double
    let langMe, langThem, conversationLang, agent, level: String
    let visible: Bool
    /// Whether CallCoach hears me, and whether it heard my voice in the last second (false while it does not hear me).
    var hearing: Bool? = nil
    var meVoice: Bool? = nil
    /// Whether the recording is paused: neither side is heard. An older engine sends none.
    var paused: Bool? = nil
    /// Whether the transcriber is ready; a call records while it is false. An older engine sends none, read as true.
    var transcribing = true
    /// The tokens the call added so far (cache reads left out), and those of each feature that asked for them.
    var tokens: Int? = nil
    var features: [String: Int]? = nil
    /// The engine's CPU, with the whisper servers, as a share of the whole Mac; nil when it could not be read.
    var cpu: Double? = nil
    /// The subscription's limit as Claude last told it: "ok", "near" or "reached".
    var limit: String? = nil
    /// The sides not being recorded now ("me", "them"), as the engine finds them (silence.py). An older engine sends
    /// none.
    var silent: [String] = []
}

extension Status {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(state: try c.decode(String.self, forKey: .state), elapsed: try c.decode(Double.self, forKey: .elapsed),
                  talkShare: try c.decode(Double.self, forKey: .talkShare), langMe: try c.decode(String.self, forKey: .langMe),
                  langThem: try c.decode(String.self, forKey: .langThem),
                  conversationLang: try c.decode(String.self, forKey: .conversationLang),
                  agent: try c.decode(String.self, forKey: .agent), level: try c.decode(String.self, forKey: .level),
                  visible: try c.decode(Bool.self, forKey: .visible), hearing: try c.decodeIfPresent(Bool.self, forKey: .hearing),
                  meVoice: try c.decodeIfPresent(Bool.self, forKey: .meVoice), paused: try c.decodeIfPresent(Bool.self, forKey: .paused),
                  transcribing: try c.decodeIfPresent(Bool.self, forKey: .transcribing) ?? true,
                  tokens: try c.decodeIfPresent(Int.self, forKey: .tokens),
                  features: try c.decodeIfPresent([String: Int].self, forKey: .features),
                  cpu: try c.decodeIfPresent(Double.self, forKey: .cpu), limit: try c.decodeIfPresent(String.self, forKey: .limit),
                  silent: try c.decodeIfPresent([String].self, forKey: .silent) ?? [])
    }
}

struct TranscriptLine: Codable, Equatable {
    let id, segmentId: Int
    let channel, lang, text: String
    let tStart, speechEnd, lineReady: Double
    let conversationLang: String
}

struct Card: Codable, Equatable, Identifiable {
    let id: Int
    let kind, text, lang: String
    let triggerLineIds: [Int]
    let requestedAt, receivedAt: Double
    let forced: Bool
    /// The Hebrew translation of a question card; empty otherwise.
    var he: String? = nil
}

struct Term: Codable, Equatable, Identifiable {
    let id: Int
    let term, note: String
    /// person, company, product or term, and for a knowledge file entry also fund, org or event; an older engine
    /// sends none.
    var kind: String? = nil
    let lang: String
    let triggerLineIds: [Int]
    /// True for an entry from a knowledge file.
    var known: Bool? = nil
    /// What we already know about a known name, newest first, without the sources.
    var ours: [String]? = nil
    /// The web link of each `ours` line's source, in the same order; "" for a line whose source has none.
    var oursLinks: [String]? = nil
    /// The knowledge file a known name came from, without .md.
    var file: String? = nil
    /// True for a known name with פרטים in its file: `explain_term` answers with them at once, and "עוד מהמודל" can
    /// ask the model for more; an older engine sends none.
    var detail: Bool? = nil
}

/// What the other side has said so far of a sentence that is still open. The line of the sentence replaces it.
struct Partial: Codable, Equatable {
    let channel, text: String
    let tStart: Double
}

/// A question in the call's language, with its Hebrew translation, which is empty when the question is Hebrew.
struct Question: Codable, Hashable {
    let text: String
    var he = ""

    init(text: String, he: String = "") {
        self.text = text
        self.he = he
    }

    /// An object `{text, he}`; a plain string is a question with no translation.
    init(from decoder: Decoder) throws {
        if let text = try? decoder.singleValueContainer().decode(String.self) {
            self.init(text: text)
            return
        }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(text: try c.decode(String.self, forKey: .text), he: try c.decodeIfPresent(String.self, forKey: .he) ?? "")
    }
}

/// The questions to ask next. They replace the shown ones; the first event of a call has none.
struct Questions: Codable, Equatable {
    let items: [Question]
    let lang: String
    let triggerLineIds: [Int]
}

/// "הם שאלו אותך": a question the other side asked me that I did not answer yet; an empty text clears it.
struct Asked: Codable, Equatable {
    let text: String
    var he = ""
    var triggerLineIds: [Int] = []

    init(text: String, he: String = "", triggerLineIds: [Int] = []) {
        self.text = text
        self.he = he
        self.triggerLineIds = triggerLineIds
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(text: try c.decode(String.self, forKey: .text), he: try c.decodeIfPresent(String.self, forKey: .he) ?? "",
                  triggerLineIds: try c.decodeIfPresent([Int].self, forKey: .triggerLineIds) ?? [])
    }
}

/// The line on what is being discussed now, at the top of "סיכום ביניים"; at most 14 Hebrew words.
struct Topic: Codable, Equatable {
    let text: String
    let triggerLineIds: [Int]
}

/// "עד עכשיו": the answer to `recap`, up to five short Hebrew lines joined by newlines.
struct Recap: Codable, Equatable {
    let ok: Bool
    let text: String
    /// An update: only what is new since this time of the call. An empty text means nothing new was said.
    var since: String? = nil
}

/// The engine's few sentences on a term whose row was opened, in answer to `explain_term`.
struct TermDetail: Codable, Equatable {
    let id: Int
    let ok: Bool
    let text: String
}

/// The answer to "מה המושג שנאמר עכשיו?" (the ids of the terms said in the last 30 seconds, newest first) or, with
/// `picked`, to the words picked from the transcript (their ids in the order picked).
struct TermsNow: Codable, Equatable {
    let ok: Bool
    let ids: [Int]
    /// True for the answer to words I picked, false for the search of what was just said.
    var picked = false

    init(ok: Bool, ids: [Int], picked: Bool = false) {
        self.ok = ok
        self.ids = ids
        self.picked = picked
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(ok: try c.decode(Bool.self, forKey: .ok), ids: try c.decode([Int].self, forKey: .ids),
                  picked: try c.decodeIfPresent(Bool.self, forKey: .picked) ?? false)
    }
}

/// The Hebrew of a line the other side said in English.
struct Translation: Codable, Equatable {
    let id: Int
    let he: String
}

/// What a screenshot of the call's window shows, in one Hebrew line; when `ok` is false, why not.
struct ScreenAnswer: Codable, Equatable {
    let ok: Bool
    let text: String
}

struct AccountState: Codable, Equatable {
    let ok: Bool
    let provider, name: String
    let viaClaudeCode: Bool
    let message: String
}

struct EngineError: Codable, Equatable {
    let code, message: String
    let endsCall: Bool
}

struct UsageEvent: Codable, Equatable {
    let call, today, month: Totals
}

struct SummaryReady: Codable, Equatable {
    /// The engine's message for a call stopped without a summary; it is not a failure.
    static let noSummary = "בלי סיכום"
    let ok: Bool
    let path, message: String

    /// A call that was stopped without a summary, like a recording only: the recording is saved and nothing failed.
    var stoppedWithoutSummary: Bool { !ok && message == Self.noSummary }
}

struct Finalized: Codable, Equatable {
    let ok: Bool
    let seconds: Double
    let reason: String
}

struct Ready: Codable, Equatable {
    let folder: String
    let replayT0: Double?
}

struct Hello: Codable, Equatable {
    let `protocol`: Int
    let version: String
}

private struct GoalsEvent: Codable { let goals: [Goal] }
private struct Head: Codable { let type: String; let t: Double? }

enum Event: Equatable {
    case hello(Hello), status(Status), ready(Ready), segment, line(TranscriptLine), partial(Partial)
    case card(Card), term(Term), termDetail(TermDetail), questions(Questions), goals([Goal])
    case asked(Asked), recap(Recap), topic(Topic), termsNow(TermsNow), screen(ScreenAnswer), translation(Translation)
    case finalized(Finalized)
    case summaryReady(SummaryReady), usage(UsageEvent)
    case account(AccountState), error(EngineError), stopped, unknown(String)

    static func decode(_ text: String) throws -> (Event, Double?) {
        let data = Data(text.utf8)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        func get<T: Decodable>(_ type: T.Type) throws -> T { try decoder.decode(type, from: data) }
        let head = try get(Head.self)
        let event: Event
        switch head.type {
        case "hello": event = .hello(try get(Hello.self))
        case "status": event = .status(try get(Status.self))
        case "ready": event = .ready(try get(Ready.self))
        case "segment": event = .segment
        case "line": event = .line(try get(TranscriptLine.self))
        case "partial": event = .partial(try get(Partial.self))
        case "card": event = .card(try get(Card.self))
        case "term": event = .term(try get(Term.self))
        case "term_detail": event = .termDetail(try get(TermDetail.self))
        case "questions": event = .questions(try get(Questions.self))
        case "asked": event = .asked(try get(Asked.self))
        case "recap": event = .recap(try get(Recap.self))
        case "topic": event = .topic(try get(Topic.self))
        case "terms_now": event = .termsNow(try get(TermsNow.self))
        case "screen": event = .screen(try get(ScreenAnswer.self))
        case "translation": event = .translation(try get(Translation.self))
        case "goals": event = .goals(try get(GoalsEvent.self).goals)
        case "finalized": event = .finalized(try get(Finalized.self))
        case "summary_ready": event = .summaryReady(try get(SummaryReady.self))
        case "usage": event = .usage(try get(UsageEvent.self))
        case "account": event = .account(try get(AccountState.self))
        case "error": event = .error(try get(EngineError.self))
        case "stopped": event = .stopped
        default: event = .unknown(head.type)
        }
        return (event, head.t)
    }
}

/// What a call starts with: the brief's header when there is one, else the call type's defaults.
struct StartFields: Equatable {
    var callType = "general"
    var lang = "auto"
    var level = "normal"
    var text = ""
    /// A file name in `<data>/briefs/`, or empty.
    var brief = ""
    var lengthMin: Double? = nil
    var goals: [Goal] = []
    var glossary: [String] = []
    /// The call folder's name after its time; the call type, so the folder tells the type.
    var name = "general"
    /// The call's title in its meta.json, or empty for the call type's label.
    var title = ""
    /// Opening questions, shown at once.
    var questions: [String] = []
    /// Only the recording and the live transcript: no agent, no summary, the panel starts hidden.
    var recordOnly = false
    /// The panel's sections the call starts with. Nil leaves the engine's own: both open.
    var sections: Sections? = nil
}

enum Command: Equatable {
    case start(StartFields, replay: String?)
    /// Ends the call; `summary` says whether the engine writes the summary.
    case stop(summary: Bool)
    case accountCheck
    case action(String), setVisibility(Bool), setLevel(String), setLanguage(String)
    case shown(cardId: Int, t: Double)
    case addContext(String)
    case explainTerm(Int)
    /// "עוד מהמודל" on a known name: the model's answer comes added under the file's detail.
    case explainMore(Int)
    /// Whether CallCoach hears me.
    case setHearing(Bool)
    /// Pauses the recording, or goes on.
    case setPaused(Bool)
    case setTranslate(Bool)
    /// Which of the panel's sections the engine computes.
    case setSections(Sections)
    /// Turns the coach on or off.
    case setCoach(Bool)
    /// Marks a goal done or not by hand.
    case setGoal(String, Bool)
    /// The goal to steer towards, or none.
    case focusGoal(String?)
    /// Asks for "עד עכשיו".
    case recap, recapSince
    /// The words picked from the transcript, each a term to look up.
    case checkTerms([String])
    /// A screenshot of the call app's window, with a note that may be empty.
    case addScreen(path: String, text: String)

    var object: [String: Any] {
        switch self {
        case .start(let f, let replay):
            var o: [String: Any] = [
                "type": "start", "call_type": f.callType, "lang": f.lang, "level": f.level, "text": f.text,
                "brief": f.brief, "length_min": f.lengthMin.map { $0 as Any } ?? (NSNull() as Any),
                "goals": f.goals.map { ["id": $0.id, "text": $0.text, "done": $0.done] }, "glossary": f.glossary,
                "name": f.name, "title": f.title, "questions": f.questions,
            ]
            if let replay { o["replay"] = replay }
            if f.recordOnly { o["record_only"] = true }
            if let s = f.sections { o["sections"] = ["ask": s.ask, "terms": s.terms, "now": s.now, "list_only": s.listOnly] }
            return o
        case .stop(let summary): return ["type": "stop", "summary": summary]
        case .accountCheck: return ["type": "account_check"]
        case .action(let id): return ["type": "action", "id": id]
        case .setVisibility(let visible): return ["type": "set_visibility", "visible": visible]
        case .setLevel(let level): return ["type": "set_level", "level": level]
        case .setLanguage(let lang): return ["type": "set_language", "lang": lang]
        case .shown(let id, let t): return ["type": "shown", "card_id": id, "t": t]
        case .addContext(let text): return ["type": "add_context", "text": text]
        case .explainTerm(let id): return ["type": "explain_term", "id": id]
        case .explainMore(let id): return ["type": "explain_term", "id": id, "more": true]
        case .checkTerms(let terms): return ["type": "check_terms", "terms": terms]
        case .addScreen(let path, let text): return ["type": "add_screen", "path": path, "text": text]
        case .setHearing(let on): return ["type": "set_hearing", "on": on]
        case .setPaused(let on): return ["type": "set_paused", "on": on]
        case .setTranslate(let on): return ["type": "set_translate", "on": on]
        case .setSections(let s):
            return ["type": "set_sections", "ask": s.ask, "terms": s.terms, "now": s.now, "list_only": s.listOnly]
        case .setCoach(let on): return ["type": "set_coach", "on": on]
        case .setGoal(let id, let done): return ["type": "set_goal", "id": id, "done": done]
        case .focusGoal(let id): return ["type": "focus_goal", "id": id ?? NSNull()]
        case .recap: return ["type": "recap"]
        case .recapSince: return ["type": "recap", "since": true]
        }
    }

    var line: String {
        let data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes]))
            ?? Data("{}".utf8)
        return String(decoding: data, as: UTF8.self) + "\n"
    }
}

extension SelfCheck {
    static func fixtures(_ root: URL, _ kind: String) -> [String: String] {
        let folder = root.appendingPathComponent("tests/protocol/\(kind)")
        let names = (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []
        var out: [String: String] = [:]
        for name in names where name.hasSuffix(".json") {
            out[String(name.dropLast(5))] = try? String(contentsOf: folder.appendingPathComponent(name), encoding: .utf8)
        }
        return out
    }

    /// A start command with the values of a fixture, so the fixture checks the field names and their encoding.
    static func startFields(_ o: [String: Any]) -> StartFields {
        let goals = (o["goals"] as? [[String: Any]] ?? []).map {
            Goal(id: $0["id"] as? String ?? "", text: $0["text"] as? String ?? "", done: $0["done"] as? Bool ?? false)
        }
        return StartFields(callType: o["call_type"] as? String ?? "", lang: o["lang"] as? String ?? "",
                           level: o["level"] as? String ?? "", text: o["text"] as? String ?? "",
                           brief: o["brief"] as? String ?? "", lengthMin: o["length_min"] as? Double, goals: goals,
                           glossary: o["glossary"] as? [String] ?? [], name: o["name"] as? String ?? "",
                           title: o["title"] as? String ?? "", questions: o["questions"] as? [String] ?? [],
                           sections: (o["sections"] as? [String: Bool]).map {
                               Sections(ask: $0["ask"] ?? true, terms: $0["terms"] ?? true, now: $0["now"] ?? false,
                                        listOnly: $0["list_only"] ?? false)
                           })
    }

    static func protocolChecks(_ root: URL) -> [String] {
        var p: [String] = []
        let events = fixtures(root, "events")
        expect(events.count == 23, "expected 23 event fixtures, found \(events.count)", &p)
        for (name, text) in events {
            guard let (event, _) = try? Event.decode(text) else { p.append("event \(name) does not decode"); continue }
            if case .unknown = event { p.append("event \(name) is unknown") }
        }
        if let text = events["hello"], let (event, _) = try? Event.decode(text), case .hello(let hello) = event {
            expect(hello.protocol == protocolVersion, "the hello fixture is protocol \(hello.protocol)", &p)
        } else {
            p.append("the hello fixture does not decode as hello")
        }
        if let text = events["partial"], let (event, _) = try? Event.decode(text), case .partial(let live) = event {
            expect(live.channel == "them" && live.tStart == 1000 && !live.text.isEmpty, "partial fixture \(live)", &p)
        } else {
            p.append("the partial fixture does not decode as a partial")
        }
        if let text = events["term_detail"], let (event, _) = try? Event.decode(text), case .termDetail(let detail) = event {
            expect(detail.ok && !detail.text.isEmpty, "term_detail fixture \(detail)", &p)
        } else {
            p.append("the term_detail fixture does not decode as term_detail")
        }
        if let text = events["asked"], let (event, _) = try? Event.decode(text), case .asked(let asked) = event {
            expect(asked == Asked(text: "How is yours different?", he: "במה שלכם שונה?", triggerLineIds: [8]), "asked fixture \(asked)", &p)
        } else {
            p.append("the asked fixture does not decode as asked")
        }
        if let text = events["recap"], let (event, _) = try? Event.decode(text), case .recap(let recap) = event {
            expect(recap.ok && recap.text.split(separator: "\n").count == 3, "recap fixture \(recap)", &p)
        } else {
            p.append("the recap fixture does not decode as recap")
        }
        if let text = events["topic"], let (event, _) = try? Event.decode(text), case .topic(let topic) = event {
            expect(topic.text.hasPrefix("דנה") && topic.triggerLineIds == [2], "topic fixture \(topic)", &p)
        } else {
            p.append("the topic fixture does not decode as topic")
        }
        if let text = events["terms_now"], let (event, _) = try? Event.decode(text), case .termsNow(let found) = event {
            expect(found == TermsNow(ok: true, ids: [4, 2], picked: false), "terms_now fixture \(found)", &p)
        } else {
            p.append("the terms_now fixture does not decode as terms_now")
        }
        if let text = events["screen"], let (event, _) = try? Event.decode(text), case .screen(let shot) = event {
            expect(shot.ok && shot.text.hasPrefix("שקף"), "screen fixture \(shot)", &p)
        } else {
            p.append("the screen fixture does not decode as screen")
        }
        if let text = events["translation"], let (event, _) = try? Event.decode(text), case .translation(let t) = event {
            expect(t.id == 12 && t.he.hasPrefix("בשבוע"), "translation fixture \(t)", &p)
        } else {
            p.append("the translation fixture does not decode as translation")
        }
        if let text = events["questions"], let (event, _) = try? Event.decode(text), case .questions(let q) = event {
            expect(q.items == [Question(text: "When did this last cause a problem?", he: "מתי זה יצר בעיה בפעם האחרונה?"),
                               Question(text: "Who else is part of the decision?", he: "מי עוד מעורב בהחלטה?")],
                   "questions fixture \(q.items)", &p)
        } else {
            p.append("the questions fixture does not decode as questions")
        }
        if let text = events["status"], let (event, _) = try? Event.decode(text), case .status(let s) = event {
            expect(s.hearing == true && s.meVoice == false && s.paused == false && s.transcribing,
                   "status fixture hearing \(String(describing: s.hearing)) paused \(String(describing: s.paused))", &p)
        } else {
            p.append("the status fixture does not decode as status")
        }
        if let text = events["card"], let (event, _) = try? Event.decode(text), case .card(let card) = event {
            expect(card.he == "" && card.kind == "question", "card fixture \(card)", &p)
        } else {
            p.append("the card fixture does not decode as a card")
        }
        let bare = #"{"type": "status", "state": "idle", "elapsed": 0, "talk_share": 0, "lang_me": "he", "lang_them": "he", "conversation_lang": "he", "agent": "off", "level": "normal", "visible": true"#
        if let (event, _) = try? Event.decode(bare + "}"), case .status(let s) = event {
            expect(s.transcribing, "a status without transcribing is transcribing", &p)
        } else {
            p.append("a status without transcribing does not decode")
        }
        if let (event, _) = try? Event.decode(bare + #", "transcribing": false}"#), case .status(let s) = event {
            expect(!s.transcribing, "a status that is not transcribing yet", &p)
        } else {
            p.append("a status with transcribing false does not decode")
        }
        if let (event, _) = try? Event.decode(#"{"type": "questions", "items": ["מה הלאה?"], "lang": "he", "trigger_line_ids": []}"#),
           case .questions(let plain) = event {
            expect(plain.items == [Question(text: "מה הלאה?")], "a plain string is a question with no translation", &p)
        } else {
            p.append("questions as plain strings do not decode")
        }
        if let (event, _) = try? Event.decode(#"{"type": "brand_new", "t": 3, "x": [1]}"#), case .unknown(let type) = event {
            var model = CallModel()
            expect(model.apply(event, now: 3).isEmpty && type == "brand_new" && model.partial == nil,
                   "an unknown event is ignored", &p)
        } else {
            p.append("an unknown event does not decode as unknown")
        }
        let stream = (try? String(contentsOf: root.appendingPathComponent("tests/protocol/stream/demo-call.jsonl"),
                                  encoding: .utf8)) ?? ""
        let lines = stream.split(separator: "\n").map(String.init)
        expect(lines.count > 30, "stream has \(lines.count) lines", &p)
        for line in lines {
            guard let (event, _) = try? Event.decode(line) else { p.append("stream line does not decode: \(line.prefix(60))"); continue }
            if case .unknown(let type) = event { p.append("stream has an unknown event \(type)") }
        }

        let made: [String: Command] = [
            "stop": .stop(summary: true), "action": .action("ask_now"), "set_visibility": .setVisibility(false),
            "set_level": .setLevel("quiet"), "set_language": .setLanguage("en"),
            "shown": .shown(cardId: 4, t: 1005.2), "account_check": .accountCheck,
            "add_context": .addContext("דנה מנהלת IT ב-Acme, רוצה להבין מי מאשר גישה"),
            "set_hearing": .setHearing(false), "set_paused": .setPaused(true), "set_translate": .setTranslate(true), "set_sections": .setSections(Sections(ask: true, terms: true, now: true, listOnly: true)),
            "set_coach": .setCoach(true), "set_goal": .setGoal("g1", true), "focus_goal": .focusGoal("g2"),
            "recap": .recap, "check_terms": .checkTerms(["אוקטה וורקפלואוס", "סקים"]),
            "add_screen": .addScreen(path: "/Users/someone/CallCoachData/calls/2026-11-17_1500_general/screens/150312.jpg",
                                     text: "זה הדמו של המוצר שלהם"),
        ]
        expect(Command.recapSince.line == #"{"since":true,"type":"recap"}"# + "\n", "an update asks for what is new \(Command.recapSince.line)", &p)
        let commands = fixtures(root, "commands")
        let objects = commands.compactMapValues { text in
            (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any]
        }
        expect(Set(commands.keys) == Set(made.keys).union(["start", "explain_term"]), "command fixtures \(commands.keys.sorted())", &p)
        var built = made
        if let start = objects["start"] { built["start"] = .start(startFields(start), replay: start["replay"] as? String) }
        if let explain = objects["explain_term"], let id = explain["id"] as? Int { built["explain_term"] = .explainTerm(id) }
        for (name, command) in built {
            guard let fixture = objects[name].map({ $0 as NSDictionary }),
                  let ours = try? JSONSerialization.jsonObject(with: Data(command.line.utf8)) as? NSDictionary else {
                p.append("command \(name) missing or not JSON"); continue
            }
            expect(fixture == ours, "command \(name) differs: \(command.line)", &p)
        }
        let keys = Set(Command.start(StartFields(), replay: nil).object.keys)
        expect(keys == ["type", "call_type", "lang", "level", "text", "brief", "length_min", "goals", "glossary", "name",
                        "title", "questions"], "start fields \(keys.sorted())", &p)
        var recording = StartFields()
        recording.recordOnly = true
        expect(Command.start(recording, replay: nil).object["record_only"] as? Bool == true, "a record-only start says so", &p)
        var sectioned = StartFields()
        expect(Command.start(sectioned, replay: nil).object["sections"] == nil, "a start with no sections leaves them to the engine", &p)
        sectioned.sections = Sections(ask: false, terms: true)
        expect(Command.start(sectioned, replay: nil).object["sections"] as? [String: Bool]
                   == ["ask": false, "terms": true, "now": false, "list_only": false],
               "a start carries the sections the call begins with", &p)
        expect(Command.setLanguage("auto").line == "{\"lang\":\"auto\",\"type\":\"set_language\"}\n"
               && Command.explainTerm(7).line == "{\"id\":7,\"type\":\"explain_term\"}\n", "set_language auto and explain_term", &p)
        expect(Command.explainMore(7).line == "{\"id\":7,\"more\":true,\"type\":\"explain_term\"}\n",
               "more from the model asks explain_term with more \(Command.explainMore(7).line)", &p)
        expect(Command.setHearing(true).line == "{\"on\":true,\"type\":\"set_hearing\"}\n"
               && Command.setSections(Sections()).line
                   == "{\"ask\":true,\"list_only\":false,\"now\":false,\"terms\":true,\"type\":\"set_sections\"}\n",
               "set_hearing and set_sections encode booleans \(Command.setHearing(true).line)", &p)
        expect(protocolVersion == 6, "protocol 6", &p)
        expect(Command.stop(summary: false).object["summary"] as? Bool == false, "stop says whether to summarize", &p)
        expect(Command.setSections(Sections(ask: true, terms: false)).object.keys.sorted() == ["ask", "list_only", "now", "terms", "type"],
               "two sections, the now line and list only", &p)
        expect(Command.setCoach(true).object["type"] as? String == "set_coach", "the coach switch", &p)
        expect(Command.setGoal("g1", true).object["done"] as? Bool == true, "a goal by hand", &p)
        expect(Command.focusGoal(nil).object["id"] is NSNull && Command.focusGoal("g2").object["id"] as? String == "g2",
               "focus or none", &p)
        expect(Command.focusGoal(nil).line == "{\"id\":null,\"type\":\"focus_goal\"}\n", "no focus encodes null \(Command.focusGoal(nil).line)", &p)
        return p
    }
}
