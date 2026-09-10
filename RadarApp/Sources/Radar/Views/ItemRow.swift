import SwiftUI

struct ItemRow: View {
    @Environment(AppState.self) private var state
    let item: Item
    var showCourse = true
    var compact = false
    @State private var noteDraft = ""
    @State private var showNote = false
    @State private var hovering = false

    private var color: Color { item.course_id.flatMap { state.courseColors[$0] } ?? .gray }
    private var ex: ItemExtra? { item.extra }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 4).padding(.vertical, 2)
            Image(systemName: item.symbol).font(.system(size: 14)).foregroundStyle(.secondary).frame(width: 18).padding(.top, 2)
                .help(Kind.label(item.kind))
            VStack(alignment: .leading, spacing: 3) {
                titleLine
                metaLine
                if !compact { detail }
            }
            Spacer(minLength: 8)
            actions.opacity(hovering ? 1 : 0.55)
        }
        .padding(.vertical, compact ? 5 : 8).padding(.horizontal, 8)
        .background(RoundedRectangle(cornerRadius: 9).fill(Color(nsColor: .windowBackgroundColor).opacity(0.7)))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(Color(nsColor: .separatorColor).opacity(0.7), lineWidth: 1))
        .onHover { hovering = $0 }
        .contextMenu { contextItems }
        .popover(isPresented: $showNote, arrowEdge: .trailing) { notePopover }
    }

    private var titleLine: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Button { state.open(item) } label: {
                Text(item.title).font(.body.weight(.semibold)).lineLimit(compact ? 1 : 2).multilineTextAlignment(.leading)
            }
            .buttonStyle(.plain)
            .disabled(item.url == nil && item.kind != "site_check")
            if let p = item.platform { PlatformBadge(name: p) }
            if item.is_new == true { Badge(text: "new", color: Palette.good, filled: true) }
            else if item.is_updated == true { Badge(text: "updated", color: Palette.warning) }
            if item.status == "missing" || item.status == "pastdue" { Badge(text: item.status_label ?? item.status, color: Palette.critical, filled: true) }
            else if item.status == "locked" { Badge(text: "locked") }
            else if item.status == "unread" { Badge(text: "unread", color: .accentColor) }
            else if let s = item.status_label, !s.isEmpty { Badge(text: s) }
            if ex?.hint == "exam" { Badge(text: "exam", color: Palette.exam, filled: true) }
            if ex?.late == true { Badge(text: "late", color: Palette.warning) }
        }
    }

    private var metaLine: some View {
        HStack(spacing: 10) {
            if showCourse, let c = item.course {
                Button { if let id = item.course_id { state.selection = .course(id) } } label: { CourseChip(name: c, color: color) }.buttonStyle(.plain)
            }
            if let d = item.due_text, !d.isEmpty {
                Text(d).foregroundStyle(item.isOverdue ? Palette.critical : .secondary).fontWeight(item.isOverdue ? .semibold : .regular)
            }
            if let p = Fmt.points(item.points) { Text(p) }
            if let s = Fmt.score(item.score, item.points) { Text(s) } else if let g = item.grade, item.status == "graded" { Text(g) }
            if let s = item.sender, !s.isEmpty { Text("from \(s)").lineLimit(1) }
            if let a = item.ago, !a.isEmpty, ["announcement", "email", "conversation", "file", "discussion"].contains(item.kind) { Text(a) }
            if let g = item.graded_ago { Text("graded \(g)") }
            if let loc = ex?.location, !loc.isEmpty { Text("@ \(loc)").lineLimit(1) }
            if !["assignment", "quiz", "syllabus_event"].contains(item.kind) { Text(Kind.label(item.kind)).opacity(0.8) }
            if item.kind == "syllabus_event" { Text(ex?.from == "syllabus-ai" ? "from syllabus (AI)" : "from syllabus") }
        }
        .font(.caption).foregroundStyle(.secondary)
    }

    @ViewBuilder private var detail: some View {
        if let s = item.ai?.summary, !s.isEmpty {
            Text(s).font(.callout).foregroundStyle(.secondary).lineLimit(3)
        } else if let b = item.body, !b.isEmpty, b != item.title,
                  ["announcement", "email", "conversation", "discussion", "syllabus_event", "planner_note", "calendar", "event", "manual", "site_check"].contains(item.kind) {
            Text(b).font(.callout).foregroundStyle(.secondary).lineLimit(3)
        }
        if let acts = item.ai?.action_items, !acts.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(acts.prefix(4)) { a in
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        Image(systemName: "arrow.turn.down.right").font(.caption2).foregroundStyle(.tertiary)
                        Text(a.text).font(.callout)
                        if let d = a.due { Text("by \(d)").font(.callout.weight(.semibold)).foregroundStyle(Palette.critical) }
                    }
                }
            }
        } else if item.kind == "email", let dates = ex?.mentioned_dates, !dates.isEmpty {
            Text("mentions \(dates.joined(separator: ", "))" + ((ex?.action_words?.isEmpty == false) ? " · \(ex!.action_words!.joined(separator: ", "))" : ""))
                .font(.caption).foregroundStyle(.tertiary)
        }
        if let n = item.note, !n.isEmpty {
            Label(n, systemImage: "note.text").font(.caption).padding(.horizontal, 6).padding(.vertical, 2)
                .background(RoundedRectangle(cornerRadius: 5).fill(Palette.warning.opacity(0.15)))
        }
    }

    private var actions: some View {
        HStack(spacing: 2) {
            if item.user_state == "done" {
                Button("Reopen") { state.reopen(item) }.buttonStyle(.bordered).controlSize(.small)
            } else {
                if item.url != nil || item.kind == "site_check" {
                    Button { state.open(item) } label: { Image(systemName: "arrow.up.right.square") }.help("Open")
                }
                Button { state.markDone(item) } label: { Image(systemName: "checkmark") }.help(item.kind == "site_check" ? "Mark checked" : "Done")
                if item.kind != "site_check" {
                    Menu { snoozeItems } label: { Image(systemName: "clock") }.menuStyle(.borderlessButton).menuIndicator(.hidden).frame(width: 26).help("Snooze")
                    Button { noteDraft = item.note ?? ""; showNote = true } label: { Image(systemName: "note.text") }.help("Note")
                }
                if item.kind == "manual" {
                    Button { state.deleteTask(item) } label: { Image(systemName: "trash") }.help("Delete")
                }
            }
        }
        .buttonStyle(.borderless)
        .controlSize(.small)
        .foregroundStyle(.secondary)
    }

    @ViewBuilder private var snoozeItems: some View {
        Button("4 hours") { state.snooze(item, hours: 4) }
        Button("Tomorrow") { state.snooze(item, hours: 24) }
        Button("3 days") { state.snooze(item, hours: 72) }
        if item.dueDate != nil { Button("Day before it's due") { state.snoozeUntilDue(item) } }
    }

    @ViewBuilder private var contextItems: some View {
        if item.url != nil { Button("Open") { state.open(item) } }
        if item.user_state == "done" { Button("Reopen") { state.reopen(item) } } else {
            Button("Mark Done") { state.markDone(item) }
            Menu("Snooze") { snoozeItems }
            Button("Add Note…") { noteDraft = item.note ?? ""; showNote = true }
        }
        if let id = item.course_id { Button("Show Course") { state.selection = .course(id) } }
        if let u = item.url { Button("Copy Link") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(u, forType: .string) } }
    }

    private var notePopover: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Note").font(.headline)
            TextEditor(text: $noteDraft).frame(width: 260, height: 90).font(.body)
            HStack {
                Spacer()
                Button("Cancel") { showNote = false }
                Button("Save") { state.setNote(item, noteDraft); showNote = false }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(12)
    }
}
