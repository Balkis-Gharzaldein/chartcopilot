# Data-to-Viz Chart Selection Reference

**Source:** [data-to-viz.com](https://www.data-to-viz.com/) and the site's
[open-source decision tree](https://github.com/holtzy/data_to_viz)

**Captured:** 2026-08-31

## Purpose

This is an implementation-ready extraction of the Data-to-Viz decision logic.
The site produces a set of feasible chart candidates, not one automatically
best chart. Data structure narrows the options; analytical purpose and
readability determine the final choice.

## 1. Input Classification

| Input | Values to determine |
|---|---|
| Data family | Numeric, categorical, mixed, map, network, time series |
| Numeric fields | 1, 2, 3, or several |
| Categorical fields | 1 or several |
| Order | Ordered sequence/time versus unordered observations |
| Sample size | Few versus many observations; the tree uses approximately 2,000 points |
| Group structure | One observation per group versus several |
| Relationship structure | Independent lists, subgroups, hierarchy, adjacency, edges |
| Geographic structure | Coordinates, regions, boundaries, origin/destination |
| Network values | No value, leaf value, edge value, or connection value |
| Series count | One series versus several series |
| Analytical goal | Distribution, relationship, ranking, part-to-whole, evolution, hierarchy, flow, map |

## 2. Core Decision Tree

### Numeric Data

| Data shape and condition | Candidate charts |
|---|---|
| One numeric variable | Histogram, density plot |
| Two numeric variables, ordered | Connected scatter, area plot, line plot |
| Two numeric variables, unordered, fewer than approximately 2,000 points | Boxplot, histogram, scatter plot |
| Two numeric variables, unordered, more than approximately 2,000 points | Violin plot, density plot, scatter with marginal points, 2D density plot |
| Three numeric variables, ordered | Stacked area, streamgraph, line plot, small-multiple area |
| Three numeric variables, unordered | Boxplot, violin plot, bubble plot, 3D scatter or surface |
| Several numeric variables, ordered | Stacked area, streamgraph, line plot, small-multiple area |
| Several numeric variables, unordered | Boxplot, violin plot, PCA, correlogram, heatmap, dendrogram, ridgeline plot |

The source does not define what happens at exactly 2,000 points.

### Categorical Data

| Data shape and condition | Candidate charts |
|---|---|
| One categorical variable | Barplot, lollipop, waffle, word cloud, doughnut, pie, treemap, circular packing |
| Two or more categorical variables forming independent lists | Venn diagram |
| Two or more categorical variables forming a hierarchy | Treemap, circular packing, sunburst, barplot, dendrogram |
| Two or more categorical variables forming subgroups | Grouped scatter, heatmap, lollipop, grouped barplot, stacked barplot, parallel plot, spider/radar plot, Sankey diagram |
| Two or more categorical variables forming an adjacency structure | Network, chord diagram, arc diagram, Sankey diagram, heatmap |

For one categorical variable, the numeric measure is usually the implicit count
of records in each category.

### Numeric and Categorical Data

#### One Numeric and One Categorical Variable

| Condition | Candidate charts |
|---|---|
| One observation per group | Boxplot, lollipop, doughnut, word cloud, pie, treemap, circular packing, waffle |
| Several observations per group | Boxplot, violin plot, ridgeline plot, density plot, histogram |

#### One Categorical and Several Numeric Variables

| Condition | Candidate charts |
|---|---|
| One value per group/sample | Grouped scatter, heatmap, lollipop, grouped barplot, stacked barplot, parallel plot, spider/radar plot, Sankey diagram |
| Several observations per sample, no numeric ordering | Grouped scatter, 2D density, boxplot, violin plot, PCA, correlogram |
| One numeric variable is ordered and can be used on the X-axis | Stacked area, area plot, streamgraph, line plot, connected scatter |

#### Several Categorical and One Numeric Variable

| Structure and condition | Candidate charts |
|---|---|
| Nested categories, one observation per leaf | Barplot, dendrogram, sunburst, circular packing, treemap |
| Nested categories, several observations per leaf | Boxplot, violin plot |
| Subgroups, one observation per combination | Grouped scatter, heatmap, lollipop, grouped barplot, stacked barplot, parallel plot, spider/radar plot, Sankey diagram |
| Subgroups, several observations per combination | Boxplot, violin plot |
| Adjacency structure | Network, chord diagram, arc diagram, Sankey diagram, heatmap |

### Map Data

| Input structure | Candidate chart |
|---|---|
| Geographic boundaries with no value encoded | Background map |
| Numeric value for each geographic region plus region boundaries | Choropleth map |
| Geographic coordinates that can be counted within equal cells | Hexbin map |
| Numeric value per hexagonal region plus hexagonal boundaries | Hexbin map |
| Latitude/longitude plus numeric value controlling size | Bubble map |
| Origin and destination coordinates, optionally with connection values | Connection map |

Map-specific choices:

- Use a choropleth when the value belongs to an administrative region.
- Use a hexbin map when unequal region sizes would bias interpretation.
- Use a bubble map when the value belongs to a point or location.
- Use a connection map when the relationship between two locations is the subject.
- Cartogram appears in the site's gallery but is not a direct leaf in the main decision tree.

### Network and Relational Data

| Input structure | Candidate charts |
|---|---|
| General edge list | Network, chord diagram, arc diagram, Sankey diagram, heatmap, hive plot |
| Adjacency matrix with logical or edge values | Network, chord diagram, arc diagram, Sankey diagram, heatmap, hive plot |
| Hierarchical data with no attached value | Dendrogram, circular packing, treemap, sunburst, Sankey diagram |
| Hierarchical data with a value for each leaf | Dendrogram, circular packing, treemap, sunburst, Sankey diagram |
| Hierarchical edge list with values on edges | Dendrogram, Sankey diagram, chord diagram |
| Hierarchical entities with values describing connections | Hierarchical edge bundling |

Network data may be directed or undirected, weighted or unweighted, and may
be represented as an edge list or adjacency matrix. A node table may provide
additional attributes.

### Time-Series Data

| Series structure | Candidate charts |
|---|---|
| One time series | Boxplot, violin plot, ridgeline plot, area plot, line plot, barplot, lollipop |
| Several time series | Boxplot, violin plot, ridgeline plot, heatmap, line plot, stacked area, streamgraph |

## 3. Choosing Between Candidates

| Analytical goal | Prefer or consider | Main guidance |
|---|---|---|
| Show one distribution | Histogram or density plot | Try different bin sizes or bandwidths |
| Compare distributions | Boxplot, violin, or ridgeline | Show sample size; use jitter for small samples |
| Show a two-variable relationship | Scatter plot | Address overplotting |
| Show a dense two-variable relationship | 2D density or hexbin | Use when the sample is sufficiently large |
| Rank categories | Barplot or lollipop | Sort categories; use horizontal orientation for long labels |
| Show change over order or time | Line or area plot | Ensure the X-axis is meaningfully ordered |
| Show total and relative composition over time | Stacked area or streamgraph | Good for totals and proportions, weak for individual series |
| Show a hierarchy | Treemap, dendrogram, circular packing, or sunburst | Use interaction when hierarchy is deep |
| Show precise category values | Barplot, lollipop, or dot plot | Avoid relying on area or angles |
| Show proportions | Prefer sorted bars; pie/doughnut only cautiously | Parts must sum to 100% |
| Show correlations | Correlogram | More than approximately 10 variables becomes difficult to read |
| Show many numeric variables | Heatmap, PCA, parallel coordinates, or dendrogram | Normalize and explain transformations |
| Show a network | Network, chord, arc, or Sankey | Control layout and reduce crossings |
| Show geographic intensity | Choropleth, hexbin, or bubble map | Normalize regional values and show a legend |

## 4. Chart-Specific Rules

### Distribution Charts

- Histograms and density plots require numeric data.
- Test multiple histogram bin sizes.
- Test multiple density bandwidths.
- Do not place more than approximately five distributions on one axis.
- Use violin or ridgeline plots when many distributions must be compared.
- Boxplots summarize distributions but hide sample size and detail.
- Show `n`, vary box width, add jitter, or use a violin plot when the underlying distribution matters.
- Use jitter for small datasets and violin plots for larger datasets.

### Scatter, Bubble, and Density Charts

- Scatterplots show the relationship between two numeric variables.
- Use transparency, smaller points, sampling, faceting, marginal distributions, or 2D density to address overplotting.
- A bubble plot uses three numeric variables: X position, Y position, and bubble size.
- Map values to bubble area, not radius or diameter.
- Always explain bubble size with a legend or direct labels.
- Use transparency when bubbles overlap.
- 2D density is intended for large point clouds.
- Connected scatterplots require a meaningful ordering of observations.
- Do not use dual axes.

### Barplots and Lollipop Charts

- Barplots are generally the clearest option for numeric values by category.
- Do not confuse a barplot with a histogram.
- Sort bars when ranking is meaningful.
- Use horizontal bars for long category labels.
- Bar axes should normally begin at zero when absolute magnitude is being compared.
- Lollipop charts are useful when many bars have similar heights and the bar fill creates clutter or a Moire effect.
- Use a conventional barplot when categories must remain unsorted.
- Keep grouped bars physically close within each group, with visible spacing between groups.

### Pie and Doughnut Charts

- Use only when parts represent a complete whole and sum to 100%.
- The site recommends avoiding pie and doughnut charts where possible.
- Prefer a sorted barplot or lollipop chart when precise comparison matters.
- Do not use 3D.
- Label slices directly instead of relying on a side legend.
- Do not place multiple pie or doughnut charts side by side for comparison.
- Verify that displayed percentages and totals are mathematically correct.

### Hierarchy Charts

- Treemaps use rectangle area to represent value.
- Treemaps use space efficiently but become difficult to annotate deeply.
- Consider interaction beyond approximately two hierarchy levels.
- Do not annotate more than approximately three hierarchy levels.
- Circular packing makes hierarchy more obvious but is poor for precise value comparison.
- Sunbursts use concentric rings but make slice comparison difficult.
- Dendrograms require an understood distance metric and clustering method.
- A dendrogram paired with a heatmap can explain both the grouping and the underlying values.
- Use horizontal layouts when labels are long.

### Evolution and Stacking

- Line charts require ordered measurements, usually time.
- Aggregate repeated measurements at the same time point where appropriate.
- Add a confidence zone when the line represents an estimated trend.
- Avoid spaghetti charts; too many series make individual trends unreadable.
- Use small multiples, highlighting, or aggregation when there are many series.
- Stacked areas are suitable for total volume and relative composition.
- Stacked areas are poor for following one individual group because the baseline changes.
- Use a percent-stacked area when relative share is more important than absolute value.
- Streamgraphs emphasize flow and proportion, not precise individual trends.
- The order of stacked groups affects interpretation; test the ordering.
- Do not use dual axes to compare unrelated variables.

### Multivariate Charts

- Correlograms become difficult to read beyond approximately 10 variables.
- Heatmaps are best for overview patterns, not precise individual-value lookup.
- Normalize data when variables are on incompatible scales.
- Consider clustering and reordering rows or columns in heatmaps.
- Use parallel coordinates instead of radar charts when comparing many observations or differently scaled variables.
- Sort variables in parallel coordinates to reduce line crossings.
- Parallel coordinates become unreadable when too many observations are drawn.
- Radar/spider charts should generally contain no more than approximately five groups.
- Keep radar variables on comparable scales or display each scale clearly.
- Axis order can change the apparent radar shape.
- Use small multiples rather than overlaying many radar series.
- A ranked barplot or lollipop is usually clearer for one series.

### Maps

- State the geographic data source and projection.
- Do not add colour unless it communicates a clear variable or distinction.
- Normalize values before comparing regions of different size or population.
- Always include a legend for encoded map values.
- A choropleth can be biased by region size; consider hexbin or bubble maps.
- Hexbin maps reduce region-area bias but remove familiar geographic landmarks.
- Map bubble size by area, not diameter.
- Use transparency when map bubbles overlap.
- For connection maps, great-circle arcs are generally preferable to straight lines.
- Draw order matters when many connections overlap.
- Use a log scale when multiplicative or percentage-change comparisons make a linear scale misleading.

### Network and Flow Charts

- Network layout strongly affects interpretation.
- Avoid the hairball problem caused by too many connections.
- In chord diagrams, order nodes to reduce crossings.
- In arc diagrams, node order is the central analytical decision.
- Sankey link width should represent flow quantity.
- Position Sankey nodes to reduce crossings.
- Consider suppressing very weak connections.
- Hierarchical edge bundling is appropriate when relationships exist among entities that already have a hierarchy.

### Text and Word Clouds

- Word clouds are useful for quickly showing prominent terms, but poor for precise comparison.
- Font size, word length, orientation, and colour can distort perceived importance.
- Use them only with a sufficiently large text sample.
- Prefer a horizontal lollipop plot when ranking terms precisely matters.

## 5. General Quality Rules

| Caveat | Rule |
|---|---|
| Ordering | Sort categories or groups when comparison is the goal |
| Axis truncation | Avoid misleadingly cut axes, especially for bars |
| Overplotting | Use transparency, sampling, aggregation, faceting, or density |
| Colour | Colour must encode data or direct attention; remove decorative colour |
| Numeric colour | Avoid rainbow palettes; use sequential or diverging scales appropriately |
| Categorical colour | Use a consistent categorical palette and avoid excessive categories |
| Legends | Make legends explain the encoding; direct-label where practical |
| Consistency | The same group should retain the same colour across charts |
| Annotation | Highlight the important finding directly on the chart |
| Calculation | Check totals, percentages, denominators, and displayed labels |
| Mental arithmetic | Do not force readers to calculate differences or proportions |
| Area encoding | Prefer position or length when exact comparison matters |
| Aspect ratio | Avoid extreme wide or tall layouts |
| 3D | Avoid static 3D except for genuine surfaces or exploratory interactive use |
| Small multiples | Use consistent scales and meaningful horizontal or vertical placement |
| Familiar conventions | Do not reverse common visual conventions without a clear reason |
| Decluttering | Remove redundant labels, gridlines, colour effects, and decoration |
| Stacking | Use stacking for totals or composition, not precise individual comparisons |
| Error bars | State exactly what the interval represents; do not treat an unexplained error bar as self-evident |
| Simpson's paradox | Check both aggregate trends and subgroup trends before interpreting a relationship |

## 6. Implementation Interpretation

The Data-to-Viz tree should be implemented as a candidate generator:

```text
classify the semantic family
-> classify variable counts
-> classify order and sample size
-> classify group or relationship structure
-> return compatible chart candidates
-> apply analytical-purpose preferences
-> apply readability and caveat warnings
-> let the analyst choose or rank the final chart
```

Hard routing rules:

- Data family.
- Number of numeric and categorical variables.
- Ordered versus unordered.
- Few versus many points.
- One versus several observations per group.
- Nested, subgroup, independent-list, adjacency, or network structure.
- Coordinates, regions, boundaries, and origin/destination.
- One versus several time series.

Soft guidance:

- Whether the chart communicates the intended analytical goal.
- Category count.
- Number of groups or distributions.
- Label length.
- Need for exact comparison.
- Need for proportions, totals, trends, or relationships.
- Accessibility, annotation, colour, legend, and layout choices.

Recommended family precedence when several classifications are technically
possible:

1. Map and geographic structure.
2. Network, adjacency, and hierarchy structure.
3. Time-series semantics.
4. Mixed numeric/categorical structure.
5. Numeric-only structure.
6. Categorical-only structure.

## 7. Source Limitations

- The site recommends candidates rather than ranking them.
- The only explicit numeric threshold is approximately 2,000 points.
- Several chart links in the original site are incomplete or inconsistent, including PCA, Waffle, Hive, grouped bar, stacked bar, and 3D.
- Cartogram and circular barplot appear in the gallery but are not direct decision-tree endpoints.
- Some branches include charts that are technically possible but questionable for the stated data shape, such as boxplots with one observation per group.
- The site's own philosophy is to identify feasible charts, try the plausible options, and choose based on both the data and the communication context.

## Source Links

- [Data-to-Viz homepage](https://www.data-to-viz.com/)
- [Data-to-Viz about page](https://www.data-to-viz.com/about.html)
- [Caveats gallery](https://www.data-to-viz.com/caveats.html)
- [Chart repository](https://github.com/holtzy/data_to_viz)
- [Pinned homepage source](https://raw.githubusercontent.com/holtzy/data_to_viz/253d428fdc4dd04c20bce6c1828448f423d27c05/index.html)
