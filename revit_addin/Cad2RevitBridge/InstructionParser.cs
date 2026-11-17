using System;
using System.Collections.Generic;
using System.Linq;

namespace Cad2RevitBridge
{
    public partial class InstructionParser
    {
        /// <summary>
        /// Bridge sends snake_case, C# members are PascalCase. The mapping is
        /// explicit instead of relying on a case-insensitive deserialiser, so
        /// the wire contract in docs/conversion-pipeline.md is the only truth.
        /// </summary>
        public static Instruction FromJson(IDictionary<string, object> row)
        {
            var instruction = new Instruction
            {
                ElementId = (string)row["element_id"],
                Op = (string)row["op"],
                Level = (string)row["level"],
                ElevationMm = Convert.ToDouble(row["elevation_mm"]),
                Family = (string)row["family"],
                Type = (string)row["type"],
                Params = row["params"] as Dictionary<string, object>
            };
            instruction.Geometry = GeometryFrom(row["geometry"] as IDictionary<string, object>);
            return instruction;
        }

        private static Geometry GeometryFrom(IDictionary<string, object> geometry)
        {
            var result = new Geometry();
            if (geometry == null) return result;
            result.Start = (geometry["start"] as IEnumerable<object>)?.Select(Convert.ToDouble).ToArray();
            result.End = (geometry["end"] as IEnumerable<object>)?.Select(Convert.ToDouble).ToArray();
            result.Insert = (geometry["insert"] as IEnumerable<object>)?.Select(Convert.ToDouble).ToArray();
            result.Section = (geometry["section"] as IEnumerable<object>)?.Select(Convert.ToDouble).ToArray();
            result.Thickness = Convert.ToDouble(geometry["thickness"] ?? 0.0);
            result.Width = Convert.ToDouble(geometry["width"] ?? 0.0);
            result.Depth = Convert.ToDouble(geometry["depth"] ?? 0.0);
            result.Rotation = Convert.ToDouble(geometry["rotation"] ?? 0.0);
            return result;
        }
    }
}
