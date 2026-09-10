import Charts
import SwiftUI

struct StatTile: View {
    let value: Int
    let label: String
    var tint: Color? = nil
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(value)").font(.system(size: 26, weight: .semibold, design: .rounded)).foregroundStyle(tint ?? .primary)
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(minWidth: 84, alignment: .leading)
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 12).fill(tint.map { $0.opacity(0.10) } ?? Color(nsColor: .controlBackgroundColor)))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(tint?.opacity(0.5) ?? Color(nsColor: .separatorColor), lineWidth: 1))
    }
}

struct Badge: View {
    let text: String
    var color: Color = .secondary
    var filled = false
    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 9.5, weight: .semibold))
            .tracking(0.3)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .foregroundStyle(filled ? .white : color)
            .background(Capsule().fill(filled ? color : color.opacity(0.12)))
    }
}

struct PlatformBadge: View {
    let name: String
    var body: some View {
        Text(name).font(.system(size: 10, weight: .semibold)).padding(.horizontal, 6).padding(.vertical, 2)
            .background(Capsule().fill(Color.accentColor.opacity(0.13))).foregroundStyle(.primary)
    }
}

struct CourseChip: View {
    let name: String
    let color: Color
    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(name).lineLimit(1)
        }
    }
}

struct WorkloadChart: View {
    let days: [WorkloadDay]
    var body: some View {
        let mx = max(1, days.map(\.count).max() ?? 1)
        Chart(days) { d in
            BarMark(x: .value("Day", d.id), y: .value("Due", d.count), width: .ratio(0.55))
                .foregroundStyle(Color.accentColor)
                .cornerRadius(3)
                .annotation(position: .top, spacing: 2) {
                    if d.count > 0 { Text("\(d.count)").font(.system(size: 10)).foregroundStyle(.secondary) }
                }
        }
        .chartYScale(domain: 0...(Double(mx) * 1.3))
        .chartYAxis(.hidden)
        .chartXAxis {
            AxisMarks(values: days.map(\.id)) { v in
                AxisValueLabel {
                    if let s = v.as(String.self), let d = days.first(where: { $0.id == s }) {
                        Text(d.label).font(.system(size: 10, weight: d.id == days.first?.id ? .bold : .regular))
                    }
                }
            }
        }
        .chartPlotStyle { $0.padding(.top, 6) }
        .frame(minWidth: 220, maxWidth: 420, minHeight: 70, maxHeight: 74)
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(nsColor: .controlBackgroundColor)))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
        .help("Tasks due per day for the next two weeks")
    }
}

struct SectionBox<Content: View>: View {
    let title: String
    let count: Int
    var tint: Color? = nil
    var collapsedByDefault = false
    var emptyText = "Nothing here."
    @ViewBuilder var content: () -> Content
    @State private var expanded: Bool? = nil

    var body: some View {
        let isOpen = expanded ?? !collapsedByDefault
        VStack(alignment: .leading, spacing: 6) {
            Button {
                withAnimation(.snappy) { expanded = !isOpen }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "chevron.right").rotationEffect(.degrees(isOpen ? 90 : 0)).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    Text(title).font(.headline).foregroundStyle(tint ?? .primary)
                    Spacer()
                    Text("\(count)").font(.caption.weight(.medium)).foregroundStyle(.secondary)
                        .padding(.horizontal, 7).padding(.vertical, 2).background(Capsule().fill(Color(nsColor: .quaternaryLabelColor).opacity(0.5)))
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 12).padding(.top, 8)
            if isOpen {
                if count == 0 {
                    Text(emptyText).font(.callout).foregroundStyle(.secondary).italic().padding(.horizontal, 14).padding(.bottom, 10)
                } else {
                    VStack(spacing: 6) { content() }.padding(.horizontal, 8).padding(.bottom, 8)
                }
            } else {
                Spacer().frame(height: 6)
            }
        }
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(nsColor: .controlBackgroundColor)))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
    }
}

struct ProblemBanner: View {
    let problem: Problem
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Palette.critical)
            VStack(alignment: .leading, spacing: 3) {
                Text("\(problem.source) sync failed \(Fmt.relative(problem.when))").font(.subheadline.weight(.semibold))
                Text(problem.message).font(.callout).foregroundStyle(.secondary).lineLimit(3)
                if let fix = problem.fix { Text(fix).font(.callout.monospaced()).textSelection(.enabled) }
            }
            Spacer()
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(Palette.critical.opacity(0.08)))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Palette.critical.opacity(0.5), lineWidth: 1))
    }
}

struct EmptyHint: View {
    let symbol: String
    let title: String
    let text: String
    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: symbol).font(.system(size: 34)).foregroundStyle(.tertiary)
            Text(title).font(.title3.weight(.semibold))
            Text(text).font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 420)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(40)
    }
}
