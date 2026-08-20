export default function Heatmap({ grid, dayLabels, hasData }) {
  if (!hasData) {
    return (
      <div className="flex min-h-[178px] flex-col items-center justify-center rounded-2xl border border-dashed border-[#cdd2d8] bg-white p-6 text-center">
        <strong className="text-lg">No timestamp data</strong>
        <span className="mt-2 text-xs text-[#7d838a]">Support load heatmap needs request timestamps to render.</span>
      </div>
    );
  }

  const max = Math.max(1, ...grid.flat());

  return (
    <div className="overflow-x-auto">
      <table className="border-collapse text-[10px]">
        <thead>
          <tr>
            <th className="sticky left-0 bg-gray-100 p-1" />
            {Array.from({ length: 24 }, (_, h) => (
              <th key={h} className="p-1 font-normal text-gray-400">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grid.map((row, dayIndex) => (
            <tr key={dayLabels[dayIndex]}>
              <th className="sticky left-0 bg-gray-100 px-2 py-1 text-right font-medium text-gray-500">{dayLabels[dayIndex]}</th>
              {row.map((count, hour) => {
                const intensity = count / max;
                return (
                  <td key={hour} className="p-0.5">
                    <div
                      title={`${dayLabels[dayIndex]} ${hour}:00 — ${count} request${count === 1 ? "" : "s"}`}
                      aria-label={`${dayLabels[dayIndex]} ${hour}:00, ${count} requests`}
                      className="h-4 w-4 rounded-sm"
                      style={{
                        background: count === 0 ? "#f1f2f4" : `rgba(234, 139, 0, ${0.15 + intensity * 0.75})`,
                      }}
                    />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
