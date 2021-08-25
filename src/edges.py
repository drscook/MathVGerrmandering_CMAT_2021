from . import *
@dataclasses.dataclass
class Edges(Variable):
    name: str = 'edges'

    def __post_init__(self):
        self.yr = self.g.shapes_yr
        self.level = self.g.level
        super().__post_init__()


    def get(self):
        exists = super().get()
        if not exists['df']:
            if not exists['tbl']:
                rpt(f'creating table')
                self.process()
            self.df = read_table(self.tbl)
        return self


    def process(self):
        query = f"""
select
    *
from (
    select
        x.geoid as geoid_x,
        y.geoid as geoid_y,        
        st_distance(x.point, y.point) as distance,
        st_length(st_intersection(x.polygon, y.polygon)) as shared_perim
    from
        {self.g.combined.tbl} as x,
        {self.g.combined.tbl} as y
    where
        x.geoid < y.geoid
        and st_intersects(x.polygon, y.polygon)
    )
where
    shared_perim > 10
order by
    geoid_x, geoid_y
"""
        load_table(self.tbl, query=query, preview_rows=0)