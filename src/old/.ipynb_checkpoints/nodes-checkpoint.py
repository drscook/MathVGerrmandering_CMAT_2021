from . import *
from .crosswalks import Crosswalks
from .assignments import Assignments
from .shapes import Shapes
from .census import Census
from .elections import Elections

@dataclasses.dataclass
class Nodes(Variable):
    name              : str = 'nodes'
    abbr              : str = 'TX'
    shapes_yr         : int = 2020
    census_yr         : int = 2020
    level             : str = 'cntyvtd'
    district_type     : str = 'cd'
    contract_thresh   : int = 0
    proposal          : str = ''
    refresh_tbl       : typing.Tuple = ()
    refresh_all       : typing.Tuple = ()
    election_filters  : typing.Tuple = (
        "office='USSen' and race='general'",
        "office='President' and race='general'",
        "office like 'USRep%' and race='general'")
    n                 : typing.Any = None


    def __post_init__(self):
        self.n = self
        check_level(self.level)
        check_district_type(self.district_type)
        check_year(self.census_yr)
        check_year(self.shapes_yr)

        self.state = states[states['abbr']==self.abbr].iloc[0]
        self.__dict__.update(self.state)
        self.yr = self.census_yr
        
        self.refresh_all = listify(self.refresh_all)
        self.refresh_tbl = listify(self.refresh_tbl) + self.refresh_all
        if len(self.refresh_tbl) > 0:
            self.refresh_tbl.append(self.name)
        super().__post_init__()


    def get(self):
        self.crosswalks    = Crosswalks(n=self)
        self.assignments   = Assignments(n=self)
        self.shapes        = Shapes(n=self)
        self.census        = Census(n=self)
        self.elections     = Elections(n=self)

        self.tbl += f'_{self.district_type}_contract{self.contract_thresh}'
        self.pq = self.tbl_to_file().with_suffix('.parquet')
        self.seats_share = f'seats_{self.district_type}'
        self.cols = {'assignments': Levels + District_types,
                     'shapes'     : ['aland', 'polygon'],
                     'census'     : ['total_pop_prop', 'seats_cd', 'seats_sldu', 'seats_sldl'] + Census_columns['data'],
                     'elections'  : [c for c in get_cols(self.elections.tbl) if c not in ['geoid', 'county']]
                    }

        if self.proposal != '':
            self.tbl = self.tbl + f'_{self.proposal.lower()}'
            
        exists = super().get()
        if not exists['tbl']:
            if not exists['raw']:
                rpt(f'creating raw table')
                self.process_raw()
            rpt(f'creating table')
            self.process()
#             self.save_tbl()
        return self


    def process_raw(self):
        A_sels = [f'A.{c}'                     for c in self.cols['assignments']]
        S_sels = [f'S.{c}'                     for c in self.cols['shapes']]
        C_sels = [f'coalesce(C.{c}, 0) as {c}' for c in self.cols['census']]
        E_sels = [f'coalesce(E.{c}, 0) as {c}' for c in self.cols['elections']]
        sels = A_sels + C_sels + E_sels + S_sels 
        query = f"""
select
    A.geoid,
    max(E.county) over (partition by cnty) as county,
    {join_str(1).join(sels)},
from
    {self.assignments.tbl} as A
left join
    {self.shapes.tbl} as S
on
    A.geoid = S.geoid
left join
    {self.census.tbl} as C
on
    A.geoid = C.geoid
left join
    {self.elections.tbl} as E
on
    A.geoid = E.geoid
"""
        load_table(self.raw, query=query, preview_rows=0)


    def process(self):
        if self.proposal != '':
            assign_orig = self.assignments.tbl
            self.assignments.tbl = assign_orig + f'_{self.district_type}_{self.proposal.lower()}'
            assign_temp = self.assignments.tbl + f'_temp'
            if not check_table(self.assignments.tbl):
                file = f'{self.proposal}.csv'
                rpt(f'loading {file} into {assign_temp}')
                self.df = pd.read_csv(self.assignments.path / f'{self.district_type}/{file}')
                self.df.columns = ['geoid', self.district_type]
                self.df = self.df.astype({'geoid':str, self.district_type:str})
                load_table(tbl=assign_temp, df=self.df)
                rpt(f'creating {self.assignments.tbl}')
                query = f"""
select
    A.* except ({self.district_type}),
    A.{self.district_type} as {self.district_type}_2010,
    B.{self.district_type} as {self.district_type}_proposed
from
    {assign_orig} as A
full outer join
    {assign_temp} as B
on
    A.geoid = B.geoid
"""
                load_table(tbl=self.assignments.tbl, query=query)
                delete_table(assign_temp)
        
        
        if self.level in ['tabblock', 'bg', 'tract', 'cnty']:
            lev = f'substring(A.{self.level}, 3)'
        else:
            lev = f'A.{self.level}'

        query_temp = f"""
select
    A.geoid,
    A.cnty,
    A.{self.seats_share},
    {lev} as level,
    B.{self.district_type},
from
    {self.raw} as A
inner join
    {self.assignments.tbl} as B
on
    A.geoid = B.geoid
"""

        if self.contract_thresh == 0:
            query_temp = f"""
select
    geoid,
    level as geoid_new,
    {self.district_type},
from (
    {subquery(query_temp, indents=1)}
    )
"""
    
        elif self.contract_thresh == 2010:
            query_temp = f"""
select
    geoid,
    case when ct = 1 then substring(cnty, 3) else level end as geoid_new,
    {self.district_type},
from (
    select
        geoid,
        level,
        {self.district_type},
        cnty,
        count(distinct {self.district_type}) over (partition by cnty) as ct,
    from (
        {subquery(query_temp, indents=2)}
        )
    )
"""
        else:
            query_temp = f"""
select
    geoid,
    case when 10 * seats < {self.contract_thresh} then substring(cnty, 3) else level end as geoid_new,
    {self.district_type}
from (
    select
        geoid,
        level,
        {self.district_type},
        cnty,
        sum({self.seats_share}) over (partition by cnty) as seats,
    from (
        {subquery(query_temp, indents=2)}
        )
    )
"""

        floats = ['total_pop_prop', 'seats_cd', 'seats_sldu', 'seats_sldl']
        sels = [f'sum({c}) as {c}' for c in floats] + [f'cast(round(sum({c})) as int) as {c}' for c in self.cols['census'] + self.cols['elections'] if c not in floats]
        
        query = f"""
select
    *,
    case when perim > 0 then round(4 * {np.pi} * aland / (perim * perim) * 100, 2) else 0 end as polsby_popper,
    case when aland > 0 then total_pop / aland else 0 end as density,
    st_centroid(polygon) as point
from (
    select
        *,
        st_perimeter(polygon) / {meters_per_mile} as perim 
    from (
        select
            geoid_new as geoid,
            max(county_new) as county,
            max(vtd_new) as vtd,
            max(district_new) as {self.district_type},
            {join_str(3).join(sels)},
            st_union_agg(polygon) as polygon,
            sum(aland) / {meters_per_mile**2} as aland
        from (
            select
                *,
                case when P_district >= (max(P_district) over (partition by geoid_new)) then {self.district_type} else NULL end as district_new,
                case when P_county   >= (max(P_county)   over (partition by geoid_new)) then county else NULL end as county_new,
                case when P_vtd      >= (max(P_vtd)      over (partition by geoid_new)) then vtd    else NULL end as vtd_new
            from (
                select
                    A.geoid_new,
                    B.cntyvtd as vtd,
                    cast(A.{self.district_type} as int) as {self.district_type},
                    B.* except ({self.district_type}),
                    sum(total_pop) over (partition by geoid_new, A.{self.district_type}) as P_district,
                    sum(total_pop) over (partition by geoid_new, county) as P_county,
                    sum(total_pop) over (partition by geoid_new, B.cntyvtd) as P_vtd,
                from (
                    {subquery(query_temp, 5)}
                    ) as A
                left join
                    {self.raw} as B
                on
                    A.geoid = B.geoid
                )
            )
        group by
            geoid
        )
    )
"""
        load_table(self.tbl, query=query, preview_rows=0)